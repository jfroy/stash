package video

import (
	"bytes"
	"context"
	"os"
	"path/filepath"
	"testing"

	"github.com/stashapp/stash/pkg/ffmpeg"
	"github.com/stashapp/stash/pkg/models"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

type testCase struct {
	captionPath    string
	expectedLang   string
	expectedResult string
}

var testCases = []testCase{
	{
		captionPath:    "/stash/video.vtt",
		expectedLang:   LangUnknown,
		expectedResult: "/stash/video.",
	},
	{
		captionPath:    "/stash/video.en.vtt",
		expectedLang:   "en",
		expectedResult: "/stash/video.", // lang code valid, remove en part
	},
	{
		captionPath:    "/stash/video.test.srt",
		expectedLang:   LangUnknown,
		expectedResult: "/stash/video.test.", // no lang code/lang code invalid test should remain
	},
	{
		captionPath:    "C:\\videos\\video.fr.srt",
		expectedLang:   "fr",
		expectedResult: "C:\\videos\\video.",
	},
	{
		captionPath:    "C:\\videos\\video.xx.srt",
		expectedLang:   LangUnknown,
		expectedResult: "C:\\videos\\video.xx.", // no lang code/lang code invalid xx should remain
	},
	{
		captionPath:    "/stash/video.ja.ass",
		expectedLang:   "ja",
		expectedResult: "/stash/video.",
	},
}

func TestGenerateCaptionCandidates(t *testing.T) {
	for _, c := range testCases {
		assert.Equal(t, c.expectedResult, getCaptionPrefix(c.captionPath))
	}
}

func TestGetCaptionsLangFromPath(t *testing.T) {
	for _, l := range testCases {
		assert.Equal(t, l.expectedLang, getCaptionsLangFromPath(l.captionPath))
	}
}

func TestSupportedCaptionFormatsConvertToWebVTT(t *testing.T) {
	tests := []struct {
		name     string
		ext      string
		contents string
		text     string
	}{
		{
			name: "SRT",
			ext:  "srt",
			contents: `1
00:00:01,000 --> 00:00:02,500
Hello from SRT
`,
			text: "Hello from SRT",
		},
		{
			name: "ASS",
			ext:  "ass",
			contents: `[Script Info]
ScriptType: v4.00+

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:02.50,Default,,0,0,0,,Hello from ASS
`,
			text: "Hello from ASS",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "captions."+tt.ext)
			require.NoError(t, os.WriteFile(path, []byte(tt.contents), 0600))

			subs, err := ReadSubs(path)
			require.NoError(t, err)

			var output bytes.Buffer
			require.NoError(t, subs.WriteToWebVTT(&output))
			assert.Contains(t, output.String(), "WEBVTT")
			assert.Contains(t, output.String(), "00:00:01.000 --> 00:00:02.500")
			assert.Contains(t, output.String(), tt.text)
		})
	}
}

func TestCaptionExts(t *testing.T) {
	assert.Equal(t, []string{"vtt", "srt", "ass"}, CaptionExts)
}

func TestEmbeddedCaptions(t *testing.T) {
	streams := make([]ffmpeg.FFProbeStream, 5)
	streams[0].Index = 2
	streams[0].CodecType = "subtitle"
	streams[0].CodecName = "subrip"
	streams[0].Tags.Language = "eng"
	streams[0].Tags.Title = "English SDH"
	streams[1].Index = 3
	streams[1].CodecType = "subtitle"
	streams[1].CodecName = "ass"
	streams[1].Tags.Language = "jpn"
	streams[2].Index = 4
	streams[2].CodecType = "subtitle"
	streams[2].CodecName = "hdmv_pgs_subtitle"
	streams[3].Index = 5
	streams[3].CodecType = "subtitle"
	streams[3].CodecName = "webvtt"
	streams[4].Index = 6
	streams[4].CodecType = "audio"
	streams[4].CodecName = "subrip"

	captions := EmbeddedCaptions(streams)
	require.Len(t, captions, 3)
	assert.Equal(t, "en", captions[0].LanguageCode)
	assert.Equal(t, "srt", captions[0].CaptionType)
	assert.Equal(t, 2, *captions[0].StreamIndex)
	assert.Equal(t, "English SDH", captions[0].Title)
	assert.Equal(t, "ja", captions[1].LanguageCode)
	assert.Equal(t, "ass", captions[1].CaptionType)
	assert.Equal(t, LangUnknown, captions[2].LanguageCode)
	assert.Equal(t, "vtt", captions[2].CaptionType)
}

func TestConvertEmbeddedCaptionRejectsInvalidInput(t *testing.T) {
	_, err := ConvertEmbeddedCaption(context.Background(), "/video/movie.mkv", -1, nil)
	assert.ErrorContains(t, err, "invalid subtitle stream index")

	_, err = ConvertEmbeddedCaption(context.Background(), "/video/movie.mkv", 3, nil)
	assert.ErrorContains(t, err, "ffmpeg not configured")
}

type captionUpdaterStub struct {
	captions []*models.VideoCaption
}

func (u *captionUpdaterStub) GetCaptions(context.Context, models.FileID) ([]*models.VideoCaption, error) {
	return u.captions, nil
}

func (u *captionUpdaterStub) UpdateCaptions(_ context.Context, _ models.FileID, captions []*models.VideoCaption) error {
	u.captions = captions
	return nil
}

func TestSyncEmbeddedCaptionsPreservesSidecars(t *testing.T) {
	oldIndex := 1
	newIndex := 4
	updater := &captionUpdaterStub{captions: []*models.VideoCaption{
		{LanguageCode: "en", Filename: "movie.en.srt", CaptionType: "srt"},
		{LanguageCode: "fr", CaptionType: "ass", StreamIndex: &oldIndex},
	}}
	videoFile := &models.VideoFile{
		BaseFile: &models.BaseFile{ID: 42, Path: "/video/movie.mkv"},
		EmbeddedCaptions: []*models.VideoCaption{
			{LanguageCode: "ja", CaptionType: "ass", StreamIndex: &newIndex},
		},
		EmbeddedCaptionsScanned: true,
	}

	require.NoError(t, SyncEmbeddedCaptions(context.Background(), videoFile, updater))
	require.Len(t, updater.captions, 2)
	assert.Equal(t, "movie.en.srt", updater.captions[0].Filename)
	assert.Equal(t, newIndex, *updater.captions[1].StreamIndex)
}
