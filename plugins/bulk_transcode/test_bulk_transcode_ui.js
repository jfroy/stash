const assert = require("assert");
const fs = require("fs");
const path = require("path");

const source = fs.readFileSync(
  path.join(__dirname, "bulk_transcode_ui.js"),
  "utf8"
);

[
  "hevc_video_extra_args",
  "av1_video_extra_args",
  "audio_extra_args",
  "target_size_percent",
  "require_smaller_output",
].forEach((name) => {
  assert(source.includes(`${name}:`), `expected DEFAULTS to include ${name}`);
  assert(source.includes(`${name}: form.${name}`), `expected toPayload to include ${name}`);
});

assert(
  !source.includes(["target", "savings", "percent"].join("_")),
  "expected old target savings setting key to be removed"
);

[
  "HEVC Video Extra Args",
  "AV1 Video Extra Args",
  "Audio Extra Args",
  "Target Size %",
  "Discard outputs that are not smaller",
].forEach((label) => {
  assert(source.includes(`label: "${label}"`), `expected form label ${label}`);
});

assert(
  !source.includes("nvidia_preset"),
  "expected NVIDIA preset setting to be removed"
);
assert(
  !source.includes("NVIDIA Preset"),
  "expected NVIDIA preset field to be removed"
);

[
  "BulkTranscodeLog",
  "StashService.queryLogs",
  "StashService.useLoggingSubscribe",
  "bulk-transcode-log-output",
  "PLUGIN_LOG_PREFIX",
].forEach((fragment) => {
  assert(
    source.includes(fragment),
    `expected log section implementation to include ${fragment}`
  );
});

assert(
  source.includes('h(JobQueue, { reloadToken: queueReloadToken })') &&
    source.includes("h(BulkTranscodeLog"),
  "expected log section below queue section"
);
