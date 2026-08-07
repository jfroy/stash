# Captions

Stash supports VTT, SRT, and ASS caption files, as well as text subtitle tracks embedded in MKV containers. SRT, ASS, and embedded text subtitles are converted to WebVTT when they are loaded by the web video player.

External captions will only be detected if they are located in the same folder as the corresponding scene file. Embedded MKV subtitles are detected when the video is scanned; force a rescan of existing videos after adding or changing an embedded track.

Ensure the caption files follow these naming conventions:

## Scene

- {scene_file_name}.{language_code}.{ext}
- {scene_file_name}.{ext}

Where `{language_code}` is defined by the [ISO-639-1](https://en.wikipedia.org/wiki/List_of_ISO_639-1_codes) (2 letters) standard and `{ext}` is `vtt`, `srt`, or `ass`. Caption files without a language code will be labeled as Unknown in the video player but will work fine.

> **Note:** WebVTT does not support every ASS styling and positioning feature. The player preserves subtitle timing and text, but some advanced ASS formatting may be omitted during conversion. Bitmap-based embedded subtitles such as PGS and VobSub are not supported.

Scenes with captions can be filtered with the `captions` criterion.

> **⚠️ Note:** If the caption file was added after the scene was initially added during scan, you will need to run a Selective scan task for it to show up.
