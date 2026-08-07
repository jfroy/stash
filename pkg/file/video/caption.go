package video

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/asticode/go-astisub"
	"github.com/stashapp/stash/pkg/ffmpeg"
	"github.com/stashapp/stash/pkg/logger"
	"github.com/stashapp/stash/pkg/models"
	"github.com/stashapp/stash/pkg/txn"
	"golang.org/x/text/language"
)

// CaptionExts contains the sidecar caption formats that can be converted to
// WebVTT for playback. Keep VTT first because browsers support it natively.
var CaptionExts = []string{"vtt", "srt", "ass"}

// to be used for captions without a language code in the filename
// ISO 639-1 uses 2 or 3 a-z chars for codes so 00 is a safe non valid choise
// https://en.wikipedia.org/wiki/List_of_ISO_639-1_codes
const LangUnknown = "00"

// GetCaptionPath generates the path of a caption
// from a given file path, wanted language and caption sufffix
func GetCaptionPath(path, lang, suffix string) string {
	ext := filepath.Ext(path)
	fn := strings.TrimSuffix(path, ext)
	captionExt := ""
	if len(lang) == 0 || lang == LangUnknown {
		captionExt = suffix
	} else {
		captionExt = lang + "." + suffix
	}
	return fn + "." + captionExt
}

// ReadSubs reads a captions file
func ReadSubs(path string) (*astisub.Subtitles, error) {
	return astisub.OpenFile(path)
}

// EmbeddedCaptions returns browser-compatible text subtitle streams found by
// ffprobe. Bitmap subtitle codecs are intentionally excluded because they
// cannot be converted to WebVTT without OCR.
func EmbeddedCaptions(streams []ffmpeg.FFProbeStream) []*models.VideoCaption {
	captionTypes := map[string]string{
		"ass":    "ass",
		"ssa":    "ass",
		"subrip": "srt",
		"webvtt": "vtt",
	}

	var ret []*models.VideoCaption
	for _, stream := range streams {
		captionType, supported := captionTypes[strings.ToLower(stream.CodecName)]
		if stream.CodecType != "subtitle" || !supported {
			continue
		}

		streamIndex := stream.Index
		ret = append(ret, &models.VideoCaption{
			LanguageCode: embeddedCaptionLanguage(stream.Tags.Language),
			CaptionType:  captionType,
			StreamIndex:  &streamIndex,
			Title:        stream.Tags.Title,
		})
	}

	return ret
}

func embeddedCaptionLanguage(code string) string {
	if code == "" {
		return LangUnknown
	}

	base, _ := language.Make(code).Base()
	if base.String() == "und" {
		return LangUnknown
	}

	return base.String()
}

// ConvertEmbeddedCaption converts a text subtitle stream to WebVTT.
func ConvertEmbeddedCaption(ctx context.Context, path string, streamIndex int, encoder *ffmpeg.FFMpeg) ([]byte, error) {
	if streamIndex < 0 {
		return nil, fmt.Errorf("invalid subtitle stream index %d", streamIndex)
	}
	if encoder == nil {
		return nil, errors.New("ffmpeg not configured")
	}

	args := []string{
		"-v", "error",
		"-i", path,
		"-map", fmt.Sprintf("0:%d", streamIndex),
		"-f", "webvtt",
		"pipe:",
	}
	return encoder.GenerateOutput(ctx, args, nil)
}

// SyncEmbeddedCaptions replaces captions discovered inside the video while
// preserving sidecar caption records.
func SyncEmbeddedCaptions(ctx context.Context, f *models.VideoFile, updater CaptionUpdater) error {
	// Files loaded from the database do not carry ffprobe stream data. Avoid
	// deleting persisted tracks when an unchanged file handler is run.
	if !f.EmbeddedCaptionsScanned {
		return nil
	}

	captions, err := updater.GetCaptions(ctx, f.ID)
	if err != nil {
		return fmt.Errorf("getting captions for file %s: %w", f.Path, err)
	}

	ret := make([]*models.VideoCaption, 0, len(captions)+len(f.EmbeddedCaptions))
	for _, caption := range captions {
		if caption.StreamIndex == nil {
			ret = append(ret, caption)
		}
	}
	ret = append(ret, f.EmbeddedCaptions...)

	if err := updater.UpdateCaptions(ctx, f.ID, ret); err != nil {
		return fmt.Errorf("updating embedded captions for file %s: %w", f.Path, err)
	}
	return nil
}

// IsValidLanguage checks whether the given string is a valid
// ISO 639 language code
func IsValidLanguage(lang string) bool {
	_, err := language.ParseBase(lang)
	return err == nil
}

// IsLangInCaptions returns true if lang is present
// in the captions
func IsLangInCaptions(lang string, ext string, captions []*models.VideoCaption) bool {
	for _, caption := range captions {
		if lang == caption.LanguageCode && ext == caption.CaptionType {
			return true
		}
	}
	return false
}

// getCaptionPrefix returns the prefix used to search for video files for the provided caption path
func getCaptionPrefix(captionPath string) string {
	basename := strings.TrimSuffix(captionPath, filepath.Ext(captionPath)) // caption filename without the extension

	// a caption file can be something like scene_filename.srt or scene_filename.en.srt
	// if a language code is present and valid remove it from the basename
	languageExt := filepath.Ext(basename)
	if len(languageExt) > 2 && IsValidLanguage(languageExt[1:]) {
		basename = strings.TrimSuffix(basename, languageExt)
	}

	return basename + "."
}

// GetCaptionsLangFromPath returns the language code from a given captions path
// If no valid language is present LangUknown is returned
func getCaptionsLangFromPath(captionPath string) string {
	langCode := LangUnknown
	basename := strings.TrimSuffix(captionPath, filepath.Ext(captionPath)) // caption filename without the extension
	languageExt := filepath.Ext(basename)
	if len(languageExt) > 2 && IsValidLanguage(languageExt[1:]) {
		langCode = languageExt[1:]
	}
	return langCode
}

type CaptionUpdater interface {
	GetCaptions(ctx context.Context, fileID models.FileID) ([]*models.VideoCaption, error)
	UpdateCaptions(ctx context.Context, fileID models.FileID, captions []*models.VideoCaption) error
}

// MatchesCaption returns true if the caption file matches the video file based on the filename
func MatchesCaption(videoPath, captionPath string) bool {
	captionPrefix := getCaptionPrefix(captionPath)
	videoPrefix := strings.TrimSuffix(videoPath, filepath.Ext(videoPath)) + "."
	return captionPrefix == videoPrefix
}

// associates captions to scene/s with the same basename
// returns true if the caption file was matched to a video file and processed, false otherwise
func AssociateCaptions(ctx context.Context, captionPath string, txnMgr txn.Manager, fqb models.FileFinder, w CaptionUpdater) bool {
	captionLang := getCaptionsLangFromPath(captionPath)

	captionPrefix := getCaptionPrefix(captionPath)
	matched := false
	if err := txn.WithTxn(ctx, txnMgr, func(ctx context.Context) error {
		var err error
		files, er := fqb.FindAllByPath(ctx, captionPrefix+"*", true)

		if er != nil {
			return fmt.Errorf("searching for scene %s: %w", captionPrefix, er)
		}

		for _, f := range files {
			// found some files
			// filter out non video files
			switch f.(type) {
			case *models.VideoFile:
				break
			default:
				continue
			}

			fileID := f.Base().ID
			path := f.Base().Path

			logger.Debugf("Matched captions to file %s", path)
			matched = true

			captions, er := w.GetCaptions(ctx, fileID)
			if er != nil {
				return fmt.Errorf("getting captions for file %s: %w", path, er)
			}

			fileExt := filepath.Ext(captionPath)
			ext := fileExt[1:]
			if !IsLangInCaptions(captionLang, ext, captions) { // only update captions if language code is not present
				newCaption := &models.VideoCaption{
					LanguageCode: captionLang,
					Filename:     filepath.Base(captionPath),
					CaptionType:  ext,
				}
				captions = append(captions, newCaption)
				er = w.UpdateCaptions(ctx, fileID, captions)
				if er != nil {
					return fmt.Errorf("updating captions for file %s: %w", path, er)
				}

				logger.Debugf("Updated captions for file %s. Added %s", path, captionLang)
			}
		}
		return err
	}); err != nil {
		logger.Error(err.Error())
	}

	return matched
}

// CleanCaptions removes non existent/accessible language codes from captions
func CleanCaptions(ctx context.Context, f *models.VideoFile, txnMgr txn.Manager, w CaptionUpdater) error {
	captions, err := w.GetCaptions(ctx, f.ID)
	if err != nil {
		return fmt.Errorf("getting captions for file %s: %w", f.Path, err)
	}

	if len(captions) == 0 {
		return nil
	}

	filePath := f.Path

	changed := false
	var newCaptions []*models.VideoCaption

	for _, caption := range captions {
		if caption.StreamIndex != nil {
			newCaptions = append(newCaptions, caption)
			continue
		}

		captionPath := caption.Path(filePath)
		_, err := os.Stat(captionPath)
		if errors.Is(err, os.ErrNotExist) {
			logger.Infof("Removing non existent caption %s for %s", caption.Filename, f.Path)
			changed = true
		} else {
			// other errors are ignored for the purposes of cleaning
			newCaptions = append(newCaptions, caption)
		}
	}

	if changed {
		fn := func(ctx context.Context) error {
			return w.UpdateCaptions(ctx, f.ID, newCaptions)
		}

		// possible that we are already in a transaction and txnMgr is nil
		// in that case just call the function directly
		if txnMgr == nil {
			err = fn(ctx)
		} else {
			err = txn.WithTxn(ctx, txnMgr, fn)
		}

		if err != nil {
			return fmt.Errorf("updating captions for file %s: %w", f.Path, err)
		}
	}

	return nil
}
