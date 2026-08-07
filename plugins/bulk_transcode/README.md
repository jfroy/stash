# Bulk Transcode In-Place

This Stash plugin bulk transcodes existing scene video files and updates the existing `files`, `video_files`, and `files_fingerprints` rows. It does not create replacement scenes, so scene metadata such as ratings, play history, tags, galleries, and custom fields remains attached to the same scene records.

## Install

Copy `plugins/bulk_transcode` into your Stash plugins directory and reload plugins.

You can either:

- Open **Settings > Tools > Bulk Transcode** to configure, save, queue, and monitor the job.
- Use the normal **Settings > Tasks > Bulk transcode videos** task button to run the saved configuration.

Leave the plugin's FFmpeg and FFprobe path overrides blank to inherit Stash's configured paths. If Stash has no path configured, the plugin falls back to `ffmpeg` and `ffprobe` on `PATH`.

## Plugin Image

Build the standalone plugin artifact image from the repository root:

```sh
docker build -f plugins/bulk_transcode/Dockerfile -t stash-bulk-transcode-plugin .
```

The image is `FROM scratch` and contains the plugin at `/plugins/bulk_transcode`, intended for image-backed volume or init-container copy workflows.

## Target Codecs

The target setting only chooses the video codec. The output container is chosen per source file:

- Sources with embedded subtitle tracks are written as `.mkv` so those subtitle tracks can be copied.
- Sources without embedded subtitle tracks are written as `.mp4`.

- `hevc`: writes H.265/HEVC video using hardware encoding when available, with `libx265` fallback.
- `av1`: writes AV1 video using hardware encoding when available, with software AV1 fallback.

Audio tracks are copied when the selected container supports the source audio codec. Unsupported audio tracks are transcoded to AAC.

## Source Filters

The plugin query can be narrowed by current source codec and broad source resolution:

- Source codec: any, H.264, HEVC, AV1, VP9, MPEG-4, or other.
- Source resolution: any, `<1080p`, `1080p`, or `4K`.

Resolution buckets use the larger stored video dimension: `<1080p` is below 1920 px, `1080p` is 1920 px up to below 3840 px, and `4K` is 3840 px or larger.

## Hardware Encoding

HEVC and AV1 output use hardware encoding by default when ffmpeg exposes a supported encoder. `hardware_encoder` accepts `auto`, `nvenc`, `qsv`, `vaapi`, `amf`, `videotoolbox`, or a concrete ffmpeg encoder name such as `hevc_nvenc` or `av1_nvenc`.

When an NVIDIA NVENC encoder is selected, the plugin also enables CUDA hardware decode and keeps decoded video frames in GPU memory for the encode path.

Advanced ffmpeg flags can be added with `hevc_video_extra_args`, `av1_video_extra_args`, and `audio_extra_args`. These values are parsed like shell commands and appended after the plugin's generated video or audio codec arguments, so NVENC presets can be set with per-codec flags such as `-preset p5`. `extra_output_args` remains available for broader output-level flags.

By default, `target_size_percent` targets an output that is 60% of the original file size. For example, a 100 MB source gets a 60 MB total output budget. The plugin uses the source file size, duration, and expected output audio bitrate to derive a video bitrate budget for ffmpeg. In savings mode, generated encoder args use `-b:v`, `-maxrate`, and `-bufsize` instead of generated CQ/CRF quality controls; NVENC also gets `-rc vbr`. Set `target_size_percent` to `0` to disable bitrate targeting and return to CQ/CRF-style quality control. `require_smaller_output` is enabled by default; if a completed transcode is not smaller than the original file, the temporary output is discarded and the original file is left unchanged.

The plugin asks ffmpeg for machine-readable command progress and maps the active command's elapsed output time into the overall Stash job progress. The progress bar on **Settings > Tools > Bulk Transcode** and the normal Stash task tracker should move while each ffmpeg command is running, not only after each file completes.

The Bulk Transcode page also shows recent plugin log lines below the queue. Each transcode logs its starting ffmpeg command and a finish line with elapsed time, outcome, and, on success, pre/post file size with savings percentage.

If the selected hardware encoder fails and `hardware_fallback` is enabled, the plugin retries the file with a software encoder. HEVC falls back to `libx265`; AV1 falls back to `libsvtav1`, `libaom-av1`, or `librav1e`, depending on what ffmpeg provides.

For VAAPI, set `hardware_device` if your render device is not `/dev/dri/renderD128`.

## Generated Files

The `generated_strategy` setting controls previews, sprites, and generated transcodes after a file's hash changes:

- `migrate`: rename generated files from the old hash to the new hash and rewrite sprite VTT references. This is fastest and is the default.
- `regenerate`: delete stale generated files for the old and new hash, then queue Stash's `metadataGenerate` job for the affected scene IDs.
- `none`: leave generated files untouched.

Use `generated_previews`, `generated_sprites`, and `generated_transcodes` to choose which generated assets are migrated or regenerated. `force_generated_transcodes` maps to Stash's `forceTranscodes` option when using `regenerate`.

## Notes

- Run with `dry_run` first. It logs the ffmpeg command it would execute without running the transcode. This operation replaces media files when dry run is disabled.
- Files inside zip archives are skipped.
- If the target path already exists, the file is skipped with an error instead of overwriting an unrelated file.
- The plugin recalculates `oshash` and, by default, `md5`. If MD5 calculation is disabled, stale MD5 fingerprints are removed.
- Matching `.srt`, `.vtt`, `.ass`, `.ssa`, and `.funscript` sidecars are renamed by default if the replacement video name requires it.
