(function () {
  const PluginApi = window.PluginApi;
  if (!PluginApi) {
    return;
  }

  const React = PluginApi.React;
  const h = React.createElement;
  const Bootstrap = PluginApi.libraries.Bootstrap;
  const Router = PluginApi.libraries.ReactRouterDOM;
  const FontAwesome = PluginApi.libraries.ReactFontAwesome;
  const Icons = PluginApi.libraries.FontAwesomeSolid;
  const StashService = PluginApi.utils.StashService;
  const useSettings = PluginApi.hooks.useSettings;
  const useToast = PluginApi.hooks.useToast;

  const {
    Alert,
    Badge,
    Button,
    ButtonGroup,
    Form,
    ProgressBar,
    Spinner,
  } = Bootstrap;
  const { Link } = Router;
  const { FontAwesomeIcon } = FontAwesome;

  const PLUGIN_ID = "bulk_transcode";
  const TASK_NAME = "Bulk transcode videos";
  const ROUTE = "/plugins/bulk-transcode";
  const PLUGIN_LOG_PREFIX = "[Plugin / Bulk Transcode In-Place]";
  const MAX_LOG_LINES = 400;

  const DEFAULTS = {
    target: "hevc",
    hardware_encoding: true,
    hardware_encoder: "auto",
    hardware_device: "",
    hardware_fallback: true,
    source_codec: "all",
    source_resolution: "all",
    scene_ids: "",
    dry_run: false,
    skip_if_target: true,
    keep_backup: false,
    rename_sidecars: true,
    calculate_md5: true,
    max_count: 0,
    ffmpeg_path: "",
    ffprobe_path: "",
    hevc_crf: 28,
    hevc_preset: "medium",
    hevc_video_extra_args: "",
    av1_crf: 32,
    av1_preset: "8",
    av1_video_extra_args: "",
    audio_extra_args: "",
    target_size_percent: 60,
    require_smaller_output: true,
    extra_output_args: "",
    generated_strategy: "migrate",
    generated_previews: true,
    generated_sprites: true,
    generated_transcodes: true,
    force_generated_transcodes: false,
  };

  const SELECT_OPTIONS = {
    target: [
      ["hevc", "HEVC"],
      ["av1", "AV1"],
    ],
    source_codec: [
      ["all", "Any"],
      ["h264", "H.264"],
      ["hevc", "HEVC"],
      ["av1", "AV1"],
      ["vp9", "VP9"],
      ["mpeg4", "MPEG-4"],
      ["other", "Other"],
    ],
    source_resolution: [
      ["all", "Any"],
      ["lt1080", "<1080p"],
      ["1080p", "1080p"],
      ["4k", "4K"],
    ],
    hardware_encoder: [
      ["auto", "Auto"],
      ["nvenc", "NVIDIA NVENC"],
      ["qsv", "Intel QSV"],
      ["vaapi", "VAAPI"],
      ["amf", "AMD AMF"],
      ["videotoolbox", "VideoToolbox"],
      ["hevc_nvenc", "hevc_nvenc"],
      ["hevc_qsv", "hevc_qsv"],
      ["hevc_vaapi", "hevc_vaapi"],
      ["hevc_amf", "hevc_amf"],
      ["hevc_videotoolbox", "hevc_videotoolbox"],
      ["av1_nvenc", "av1_nvenc"],
      ["av1_qsv", "av1_qsv"],
      ["av1_vaapi", "av1_vaapi"],
      ["av1_amf", "av1_amf"],
      ["av1_videotoolbox", "av1_videotoolbox"],
    ],
    generated_strategy: [
      ["migrate", "Migrate generated files"],
      ["regenerate", "Regenerate generated files"],
      ["none", "Leave generated files alone"],
    ],
    hevc_preset: [
      ["ultrafast", "ultrafast"],
      ["superfast", "superfast"],
      ["veryfast", "veryfast"],
      ["faster", "faster"],
      ["fast", "fast"],
      ["medium", "medium"],
      ["slow", "slow"],
      ["slower", "slower"],
      ["veryslow", "veryslow"],
    ],
    av1_preset: [
      ["4", "4"],
      ["5", "5"],
      ["6", "6"],
      ["7", "7"],
      ["8", "8"],
      ["9", "9"],
      ["10", "10"],
      ["11", "11"],
      ["12", "12"],
    ],
  };

  const SELECT_SETTING_NAMES = new Set([
    "target",
    "source_codec",
    "source_resolution",
    "hardware_encoder",
    "generated_strategy",
    "hevc_preset",
    "av1_preset",
  ]);

  function asBool(value, fallback) {
    if (value === undefined || value === null || value === "") {
      return fallback;
    }
    if (typeof value === "boolean") {
      return value;
    }
    if (typeof value === "number") {
      return value !== 0;
    }
    return ["1", "true", "yes", "y", "on"].includes(
      String(value).trim().toLowerCase()
    );
  }

  function asNumber(value, fallback) {
    if (value === undefined || value === null || value === "") {
      return fallback;
    }
    const parsed = Number.parseInt(value, 10);
    return Number.isFinite(parsed) ? parsed : fallback;
  }

  function asString(value, fallback) {
    if (value === undefined || value === null) {
      return fallback;
    }
    return String(value);
  }

  function normalizeSettings(settings) {
    const merged = Object.assign({}, DEFAULTS, settings || {});
    return {
      target: asString(merged.target, DEFAULTS.target),
      hardware_encoding: asBool(
        merged.hardware_encoding,
        DEFAULTS.hardware_encoding
      ),
      hardware_encoder: asString(
        merged.hardware_encoder,
        DEFAULTS.hardware_encoder
      ),
      hardware_device: asString(
        merged.hardware_device,
        DEFAULTS.hardware_device
      ),
      hardware_fallback: asBool(
        merged.hardware_fallback,
        DEFAULTS.hardware_fallback
      ),
      source_codec: asString(merged.source_codec, DEFAULTS.source_codec),
      source_resolution: asString(
        merged.source_resolution,
        DEFAULTS.source_resolution
      ),
      scene_ids: asString(merged.scene_ids, DEFAULTS.scene_ids),
      dry_run: asBool(merged.dry_run, DEFAULTS.dry_run),
      skip_if_target: asBool(merged.skip_if_target, DEFAULTS.skip_if_target),
      keep_backup: asBool(merged.keep_backup, DEFAULTS.keep_backup),
      rename_sidecars: asBool(
        merged.rename_sidecars,
        DEFAULTS.rename_sidecars
      ),
      calculate_md5: asBool(merged.calculate_md5, DEFAULTS.calculate_md5),
      max_count: asNumber(merged.max_count, DEFAULTS.max_count),
      ffmpeg_path: asString(merged.ffmpeg_path, DEFAULTS.ffmpeg_path),
      ffprobe_path: asString(merged.ffprobe_path, DEFAULTS.ffprobe_path),
      hevc_crf: asNumber(merged.hevc_crf, DEFAULTS.hevc_crf),
      hevc_preset: asString(merged.hevc_preset, DEFAULTS.hevc_preset),
      hevc_video_extra_args: asString(
        merged.hevc_video_extra_args,
        DEFAULTS.hevc_video_extra_args
      ),
      av1_crf: asNumber(merged.av1_crf, DEFAULTS.av1_crf),
      av1_preset: asString(merged.av1_preset, DEFAULTS.av1_preset),
      av1_video_extra_args: asString(
        merged.av1_video_extra_args,
        DEFAULTS.av1_video_extra_args
      ),
      audio_extra_args: asString(
        merged.audio_extra_args,
        DEFAULTS.audio_extra_args
      ),
      target_size_percent: asNumber(
        merged.target_size_percent,
        DEFAULTS.target_size_percent
      ),
      require_smaller_output: asBool(
        merged.require_smaller_output,
        DEFAULTS.require_smaller_output
      ),
      extra_output_args: asString(
        merged.extra_output_args,
        DEFAULTS.extra_output_args
      ),
      generated_strategy: asString(
        merged.generated_strategy,
        DEFAULTS.generated_strategy
      ),
      generated_previews: asBool(
        merged.generated_previews,
        DEFAULTS.generated_previews
      ),
      generated_sprites: asBool(
        merged.generated_sprites,
        DEFAULTS.generated_sprites
      ),
      generated_transcodes: asBool(
        merged.generated_transcodes,
        DEFAULTS.generated_transcodes
      ),
      force_generated_transcodes: asBool(
        merged.force_generated_transcodes,
        DEFAULTS.force_generated_transcodes
      ),
    };
  }

  function toPayload(form) {
    return {
      target: form.target,
      hardware_encoding: form.hardware_encoding,
      hardware_encoder: form.hardware_encoder,
      hardware_device: form.hardware_device.trim(),
      hardware_fallback: form.hardware_fallback,
      source_codec: form.source_codec,
      source_resolution: form.source_resolution,
      scene_ids: form.scene_ids.trim(),
      dry_run: form.dry_run,
      skip_if_target: form.skip_if_target,
      keep_backup: form.keep_backup,
      rename_sidecars: form.rename_sidecars,
      calculate_md5: form.calculate_md5,
      max_count: form.max_count,
      ffmpeg_path: form.ffmpeg_path.trim(),
      ffprobe_path: form.ffprobe_path.trim(),
      hevc_crf: form.hevc_crf,
      hevc_preset: form.hevc_preset.trim(),
      hevc_video_extra_args: form.hevc_video_extra_args.trim(),
      av1_crf: form.av1_crf,
      av1_preset: form.av1_preset.trim(),
      av1_video_extra_args: form.av1_video_extra_args.trim(),
      audio_extra_args: form.audio_extra_args.trim(),
      target_size_percent: form.target_size_percent,
      require_smaller_output: form.require_smaller_output,
      extra_output_args: form.extra_output_args.trim(),
      generated_strategy: form.generated_strategy,
      generated_previews: form.generated_previews,
      generated_sprites: form.generated_sprites,
      generated_transcodes: form.generated_transcodes,
      force_generated_transcodes: form.force_generated_transcodes,
    };
  }

  function fieldId(name) {
    return "bulk-transcode-" + name.replace(/_/g, "-");
  }

  function TextField({ form, name, label, type, onChange, disabled, placeholder }) {
    const id = fieldId(name);
    return h(
      Form.Group,
      { className: "bulk-transcode-field" },
      h(Form.Label, { htmlFor: id }, label),
      h(Form.Control, {
        id,
        type: type || "text",
        value: form[name],
        disabled: disabled,
        placeholder: placeholder,
        onChange: (event) => onChange(name, event.currentTarget.value),
      })
    );
  }

  function NumberField({ form, name, label, onChange, min, max, disabled }) {
    const id = fieldId(name);
    return h(
      Form.Group,
      { className: "bulk-transcode-field" },
      h(Form.Label, { htmlFor: id }, label),
      h(Form.Control, {
        id,
        type: "number",
        min,
        max,
        value: form[name],
        disabled: disabled,
        onChange: (event) =>
          onChange(name, Number.parseInt(event.currentTarget.value || "0", 10)),
      })
    );
  }

  function SelectField({ form, name, label, options, onChange, disabled }) {
    const id = fieldId(name);
    return h(
      Form.Group,
      { className: "bulk-transcode-field" },
      h(Form.Label, { htmlFor: id }, label),
      h(
        Form.Control,
        {
          id,
          as: "select",
          value: form[name],
          disabled: disabled,
          onChange: (event) => onChange(name, event.currentTarget.value),
        },
        options.map(([value, text]) => h("option", { key: value, value }, text))
      )
    );
  }

  function CheckField({ form, name, label, onChange, disabled }) {
    const id = fieldId(name);
    return h(Form.Check, {
      id,
      type: "switch",
      className: "bulk-transcode-check",
      label,
      checked: !!form[name],
      disabled: disabled,
      onChange: () => onChange(name, !form[name]),
    });
  }

  function pluginSettingId(name) {
    return "plugin-" + PLUGIN_ID + "-" + name;
  }

  function pluginSettingHeading(setting) {
    return setting.display_name || setting.displayName || setting.name;
  }

  function pluginSettingSubHeading(setting) {
    return setting.description || undefined;
  }

  function savePluginSetting(settingsContext, currentSettings, name, value) {
    settingsContext.savePluginSettings(
      PLUGIN_ID,
      Object.assign({}, currentSettings || {}, { [name]: value })
    );
  }

  function renderOptionElements(options) {
    return options.map(([value, text]) => h("option", { key: value, value }, text));
  }

  function renderBulkTranscodePluginSetting({
    setting,
    normalizedSettings,
    pluginSettings,
    settingsContext,
  }) {
    const components = PluginApi.components;
    const commonProps = {
      heading: pluginSettingHeading(setting),
      id: pluginSettingId(setting.name),
      subHeading: pluginSettingSubHeading(setting),
    };
    const saveValue = (value) =>
      savePluginSetting(settingsContext, pluginSettings, setting.name, value);

    if (
      SELECT_SETTING_NAMES.has(setting.name) &&
      SELECT_OPTIONS[setting.name] &&
      components.Setting
    ) {
      return h(
        components.Setting,
        {
          key: setting.name,
          ...commonProps,
        },
        h(
          Form.Control,
          {
            as: "select",
            className: "input-control",
            value: normalizedSettings[setting.name],
            onChange: (event) => saveValue(event.currentTarget.value),
          },
          renderOptionElements(SELECT_OPTIONS[setting.name])
        )
      );
    }

    if (setting.type === "BOOLEAN" && components.BooleanSetting) {
      return h(components.BooleanSetting, {
        key: setting.name,
        checked: asBool(normalizedSettings[setting.name], DEFAULTS[setting.name]),
        onChange: saveValue,
        ...commonProps,
      });
    }

    if (setting.type === "NUMBER" && components.NumberSetting) {
      return h(components.NumberSetting, {
        key: setting.name,
        value: asNumber(normalizedSettings[setting.name], DEFAULTS[setting.name] || 0),
        onChange: saveValue,
        ...commonProps,
      });
    }

    if (components.StringSetting) {
      return h(components.StringSetting, {
        key: setting.name,
        value: asString(normalizedSettings[setting.name], DEFAULTS[setting.name] || ""),
        onChange: saveValue,
        ...commonProps,
      });
    }

    return null;
  }

  function BulkTranscodePluginSettings({ settings }) {
    const settingsContext = useSettings();
    const pluginSettings =
      (settingsContext.plugins && settingsContext.plugins[PLUGIN_ID]) || {};
    const normalizedSettings = normalizeSettings(pluginSettings);

    return h(
      "div",
      { className: "plugin-settings bulk-transcode-plugin-settings" },
      (settings || []).map((setting) =>
        renderBulkTranscodePluginSetting({
          setting,
          normalizedSettings,
          pluginSettings,
          settingsContext,
        })
      )
    );
  }

  function Section({ title, children }) {
    return h(
      "section",
      { className: "bulk-transcode-section" },
      h("h2", null, title),
      children
    );
  }

  function formGrid(children) {
    return h("div", { className: "bulk-transcode-grid" }, children);
  }

  function isTerminal(status) {
    return ["FINISHED", "CANCELLED", "FAILED"].includes(status);
  }

  function statusVariant(status) {
    switch (status) {
      case "RUNNING":
        return "primary";
      case "READY":
        return "secondary";
      case "FINISHED":
        return "success";
      case "FAILED":
        return "danger";
      case "CANCELLED":
        return "warning";
      case "STOPPING":
        return "warning";
      default:
        return "secondary";
    }
  }

  function jobIsBulkTranscode(job) {
    return String(job.description || "")
      .toLowerCase()
      .includes("bulk transcode");
  }

  function containsPropValue(node, propName, propValue) {
    if (!node) {
      return false;
    }
    if (Array.isArray(node)) {
      return node.some((child) =>
        containsPropValue(child, propName, propValue)
      );
    }
    if (typeof node !== "object") {
      return false;
    }
    if (node.props && node.props[propName] === propValue) {
      return true;
    }
    if (!node.props) {
      return false;
    }
    return Object.keys(node.props).some((key) =>
      containsPropValue(node.props[key], propName, propValue)
    );
  }

  function JobQueue({ reloadToken }) {
    const Toast = useToast();
    const jobStatus = StashService.useJobQueue();
    const jobsSubscribe = StashService.useJobsSubscribe();
    const [queue, setQueue] = React.useState([]);
    const [showAll, setShowAll] = React.useState(false);

    React.useEffect(() => {
      setQueue((jobStatus.data && jobStatus.data.jobQueue) || []);
    }, [jobStatus.data]);

    React.useEffect(() => {
      if (reloadToken && jobStatus.refetch) {
        jobStatus.refetch();
      }
    }, [reloadToken]);

    React.useEffect(() => {
      if (!jobsSubscribe.data) {
        return;
      }

      const event = jobsSubscribe.data.jobsSubscribe;
      const updateJob = () => {
        setQueue((current) =>
          current.map((job) => (job.id === event.job.id ? event.job : job))
        );
      };

      switch (event.type) {
        case "ADD":
          setQueue((current) => current.concat([event.job]));
          break;
        case "REMOVE":
          updateJob();
          window.setTimeout(() => {
            setQueue((current) =>
              current.filter((job) => job.id !== event.job.id)
            );
          }, 10000);
          break;
        case "UPDATE":
          updateJob();
          break;
        default:
          break;
      }
    }, [jobsSubscribe.data]);

    async function stopJob(job) {
      try {
        await StashService.mutateStopJob(job.id);
      } catch (error) {
        Toast.error(error);
      }
    }

    const visibleQueue = showAll ? queue : queue.filter(jobIsBulkTranscode);

    return h(
      Section,
      { title: "Queue" },
      h(
        "div",
        { className: "bulk-transcode-queue-header" },
        h(
          Button,
          {
            variant: "secondary",
            size: "sm",
            onClick: () => setShowAll(!showAll),
          },
          showAll ? "Show bulk transcode jobs" : "Show all jobs"
        )
      ),
      visibleQueue.length
        ? h(
            "div",
            { className: "bulk-transcode-job-list" },
            visibleQueue.map((job) => {
              const progress = job.progress ? job.progress * 100 : 0;
              const canStop =
                job.status === "READY" || job.status === "RUNNING";
              return h(
                "div",
                {
                  className:
                    "bulk-transcode-job" +
                    (jobIsBulkTranscode(job) ? " current-plugin-job" : ""),
                  key: job.id,
                },
                h(
                  "div",
                  { className: "bulk-transcode-job-main" },
                  h(
                    "div",
                    { className: "bulk-transcode-job-title" },
                    h(Badge, { variant: statusVariant(job.status) }, job.status),
                    h("span", null, job.description)
                  ),
                  canStop
                    ? h(
                        Button,
                        {
                          variant: "outline-danger",
                          size: "sm",
                          onClick: () => stopJob(job),
                        },
                        h(FontAwesomeIcon, { icon: Icons.faTimes })
                      )
                    : null
                ),
                job.status === "RUNNING" &&
                  job.progress !== null &&
                  job.progress !== undefined
                  ? h(ProgressBar, {
                      animated: true,
                      now: progress,
                      label: Math.round(progress) + "%",
                    })
                  : null,
                job.subTasks && job.subTasks.length
                  ? h(
                      "div",
                      { className: "bulk-transcode-subtasks" },
                      job.subTasks.slice(-3).map((task, index) =>
                        h("div", { key: index }, task)
                      )
                    )
                  : null,
                isTerminal(job.status) && job.error
                  ? h("div", { className: "bulk-transcode-job-error" }, job.error)
                  : null
              );
            })
          )
        : h(
            "div",
            { className: "bulk-transcode-empty" },
            showAll ? "No jobs in the Stash queue." : "No bulk transcode jobs in the queue."
          )
    );
  }

  function logEntryKey(entry) {
    return [entry.time || "", entry.level || "", entry.message || ""].join("|");
  }

  function logTime(entry) {
    const date = new Date(entry.time);
    if (Number.isNaN(date.valueOf())) {
      return "";
    }
    return date.toLocaleString();
  }

  function logMatchesBulkTranscode(entry) {
    const message = String((entry && entry.message) || "");
    return (
      message.includes(PLUGIN_LOG_PREFIX) ||
      message.includes("Starting transcode ") ||
      message.includes("Finished transcode ") ||
      message.includes("Would run ffmpeg:")
    );
  }

  function formatLogEntry(entry) {
    const timestamp = logTime(entry);
    const level = entry.level ? String(entry.level).toUpperCase() : "INFO";
    const message = String(entry.message || "");
    return (timestamp ? "[" + timestamp + "] " : "") + level + " " + message;
  }

  function appendLogEntries(current, incoming) {
    const seen = new Set(current.map(logEntryKey));
    const next = current.slice();
    (incoming || []).forEach((entry) => {
      if (!logMatchesBulkTranscode(entry)) {
        return;
      }
      const key = logEntryKey(entry);
      if (seen.has(key)) {
        return;
      }
      seen.add(key);
      next.push(entry);
    });
    return next.slice(-MAX_LOG_LINES);
  }

  function BulkTranscodeLog() {
    const loggingSubscribe = StashService.useLoggingSubscribe();
    const [entries, setEntries] = React.useState([]);

    React.useEffect(() => {
      let active = true;

      async function loadInitialLogs() {
        try {
          const result = await StashService.queryLogs();
          if (!active || !result || result.error) {
            return;
          }
          const logs = (result.data && result.data.logs) || [];
          setEntries((current) => appendLogEntries(current, logs));
        } catch (_error) {
          if (active) {
            setEntries((current) => current);
          }
        }
      }

      loadInitialLogs();
      return () => {
        active = false;
      };
    }, []);

    React.useEffect(() => {
      if (!loggingSubscribe.data) {
        return;
      }
      const logs = loggingSubscribe.data.loggingSubscribe || [];
      setEntries((current) => appendLogEntries(current, logs));
    }, [loggingSubscribe.data]);

    const text = entries.map(formatLogEntry).join("\n");

    return h(
      Section,
      { title: "Logs" },
      h(Form.Control, {
        as: "textarea",
        className: "bulk-transcode-log-output",
        readOnly: true,
        rows: 14,
        value: text,
        placeholder: "No bulk transcode logs yet.",
      }),
      loggingSubscribe.error
        ? h(
            "div",
            { className: "bulk-transcode-job-error" },
            "Error connecting to log stream: " + loggingSubscribe.error.message
          )
        : null
    );
  }

  function BulkTranscodePage() {
    const Toast = useToast();
    const config = StashService.useConfiguration();
    const [configurePlugin] = PluginApi.GQL.useConfigurePluginMutation();
    const [runPluginTask] = PluginApi.GQL.useRunPluginTaskMutation();
    const [form, setForm] = React.useState(DEFAULTS);
    const [initialized, setInitialized] = React.useState(false);
    const [saving, setSaving] = React.useState(false);
    const [running, setRunning] = React.useState(false);
    const [lastJobID, setLastJobID] = React.useState("");
    const [queueReloadToken, setQueueReloadToken] = React.useState(0);

    React.useEffect(() => {
      if (initialized || !config.data || !config.data.configuration) {
        return;
      }
      const plugins = config.data.configuration.plugins || {};
      setForm(normalizeSettings(plugins[PLUGIN_ID]));
      setInitialized(true);
    }, [config.data, initialized]);

    function setValue(name, value) {
      setForm((current) => Object.assign({}, current, { [name]: value }));
    }

    async function saveSettings(payload) {
      await configurePlugin({
        variables: {
          plugin_id: PLUGIN_ID,
          input: payload,
        },
      });
      if (config.refetch) {
        await config.refetch();
      }
    }

    async function onSave() {
      const payload = toPayload(form);
      setSaving(true);
      try {
        await saveSettings(payload);
        Toast.success("Bulk transcode settings saved.");
      } catch (error) {
        Toast.error(error);
      } finally {
        setSaving(false);
      }
    }

    async function onRun() {
      const payload = toPayload(form);
      setRunning(true);
      try {
        await saveSettings(payload);
        const result = await runPluginTask({
          variables: {
            plugin_id: PLUGIN_ID,
            task_name: TASK_NAME,
            args_map: payload,
          },
        });
        const jobID = result.data && result.data.runPluginTask;
        setLastJobID(jobID || "");
        setQueueReloadToken((current) => current + 1);
        Toast.success("Bulk transcode job queued.");
      } catch (error) {
        Toast.error(error);
      } finally {
        setRunning(false);
      }
    }

    const busy = saving || running || config.loading || !initialized;
    const targetIsHEVC = form.target === "hevc";
    const targetIsAV1 = form.target === "av1";
    const generatedEnabled = form.generated_strategy !== "none";
    const regenerateEnabled = form.generated_strategy === "regenerate";
    const general =
      (config.data &&
        config.data.configuration &&
        config.data.configuration.general) ||
      {};
    const ffmpegPlaceholder = general.ffmpegPath
      ? "Blank uses Stash: " + general.ffmpegPath
      : "Blank uses Stash/default ffmpeg";
    const ffprobePlaceholder = general.ffprobePath
      ? "Blank uses Stash: " + general.ffprobePath
      : "Blank uses Stash/default ffprobe";

    return h(
      "div",
      { className: "bulk-transcode-page" },
      h(
        "div",
        { className: "bulk-transcode-titlebar" },
        h("div", null, h("h1", null, "Bulk Transcode In-Place")),
        h(
          ButtonGroup,
          null,
          h(
            Button,
            {
              variant: "secondary",
              disabled: busy,
              onClick: onSave,
            },
            saving ? h(Spinner, { animation: "border", size: "sm" }) : "Save"
          ),
          h(
            Button,
            {
              variant: form.dry_run ? "info" : "primary",
              disabled: busy,
              onClick: onRun,
            },
            running
              ? h(Spinner, { animation: "border", size: "sm" })
              : form.dry_run
              ? "Queue Dry Run"
              : "Queue Transcode"
          )
        )
      ),
      form.dry_run
        ? h(Alert, { variant: "info" }, "Dry run is enabled; files and database rows will not be changed.")
        : h(Alert, { variant: "warning" }, "This job replaces media files in place and updates existing file rows."),
      lastJobID
        ? h(
            Alert,
            { variant: "secondary" },
            "Queued Stash job ",
            h("code", null, lastJobID),
            "."
          )
        : null,
      h(
        Form,
        null,
        h(
          Section,
          { title: "Query" },
          formGrid([
            h(SelectField, {
              key: "target",
              form,
              name: "target",
              label: "Target Codec",
              options: SELECT_OPTIONS.target,
              onChange: setValue,
            }),
            h(SelectField, {
              key: "source_codec",
              form,
              name: "source_codec",
              label: "Source Codec",
              options: SELECT_OPTIONS.source_codec,
              onChange: setValue,
            }),
            h(SelectField, {
              key: "source_resolution",
              form,
              name: "source_resolution",
              label: "Source Resolution",
              options: SELECT_OPTIONS.source_resolution,
              onChange: setValue,
            }),
            h(TextField, {
              key: "scene_ids",
              form,
              name: "scene_ids",
              label: "Scene IDs",
              onChange: setValue,
            }),
            h(NumberField, {
              key: "max_count",
              form,
              name: "max_count",
              label: "Max Count",
              min: 0,
              onChange: setValue,
            }),
          ]),
          h(
            "div",
            { className: "bulk-transcode-check-grid" },
            h(CheckField, {
              form,
              name: "dry_run",
              label: "Dry run",
              onChange: setValue,
            }),
            h(CheckField, {
              form,
              name: "skip_if_target",
              label: "Skip files already in target codec and container",
              onChange: setValue,
            }),
            h(CheckField, {
              form,
              name: "keep_backup",
              label: "Keep original backups",
              onChange: setValue,
            })
          )
        ),
        h(
          Section,
          { title: "Encoding" },
          formGrid([
            h(SelectField, {
              key: "hardware_encoder",
              form,
              name: "hardware_encoder",
              label: "Hardware Encoder",
              options: SELECT_OPTIONS.hardware_encoder,
              onChange: setValue,
              disabled: !form.hardware_encoding,
            }),
            h(TextField, {
              key: "hardware_device",
              form,
              name: "hardware_device",
              label: "Hardware Device",
              onChange: setValue,
              disabled: !form.hardware_encoding,
            }),
            h(NumberField, {
              key: "hevc_crf",
              form,
              name: "hevc_crf",
              label: "HEVC Quality",
              min: 0,
              max: 51,
              onChange: setValue,
              disabled: !targetIsHEVC,
            }),
            h(SelectField, {
              key: "hevc_preset",
              form,
              name: "hevc_preset",
              label: "x265 Preset",
              options: SELECT_OPTIONS.hevc_preset,
              onChange: setValue,
              disabled: !targetIsHEVC,
            }),
            h(TextField, {
              key: "hevc_video_extra_args",
              form,
              name: "hevc_video_extra_args",
              label: "HEVC Video Extra Args",
              onChange: setValue,
              disabled: !targetIsHEVC,
            }),
            h(NumberField, {
              key: "av1_crf",
              form,
              name: "av1_crf",
              label: "AV1 Quality",
              min: 0,
              max: 63,
              onChange: setValue,
              disabled: !targetIsAV1,
            }),
            h(SelectField, {
              key: "av1_preset",
              form,
              name: "av1_preset",
              label: "AV1 Preset",
              options: SELECT_OPTIONS.av1_preset,
              onChange: setValue,
              disabled: !targetIsAV1,
            }),
            h(TextField, {
              key: "av1_video_extra_args",
              form,
              name: "av1_video_extra_args",
              label: "AV1 Video Extra Args",
              onChange: setValue,
              disabled: !targetIsAV1,
            }),
            h(TextField, {
              key: "audio_extra_args",
              form,
              name: "audio_extra_args",
              label: "Audio Extra Args",
              onChange: setValue,
            }),
            h(NumberField, {
              key: "target_size_percent",
              form,
              name: "target_size_percent",
              label: "Target Size %",
              min: 0,
              max: 95,
              onChange: setValue,
            }),
          ]),
          h(
            "div",
            { className: "bulk-transcode-check-grid" },
            h(CheckField, {
              form,
              name: "hardware_encoding",
              label: "Use hardware encoding",
              onChange: setValue,
            }),
            h(CheckField, {
              form,
              name: "hardware_fallback",
              label: "Fallback to software encoder",
              onChange: setValue,
              disabled: !form.hardware_encoding,
            }),
            h(CheckField, {
              form,
              name: "require_smaller_output",
              label: "Discard outputs that are not smaller",
              onChange: setValue,
            })
          )
        ),
        h(
          Section,
          { title: "Paths And FFmpeg" },
          formGrid([
            h(TextField, {
              key: "ffmpeg_path",
              form,
              name: "ffmpeg_path",
              label: "FFmpeg Path Override",
              placeholder: ffmpegPlaceholder,
              onChange: setValue,
            }),
            h(TextField, {
              key: "ffprobe_path",
              form,
              name: "ffprobe_path",
              label: "FFprobe Path Override",
              placeholder: ffprobePlaceholder,
              onChange: setValue,
            }),
            h(TextField, {
              key: "extra_output_args",
              form,
              name: "extra_output_args",
              label: "Extra Output Args",
              onChange: setValue,
            }),
          ]),
          h(
            "div",
            { className: "bulk-transcode-check-grid" },
            h(CheckField, {
              form,
              name: "rename_sidecars",
              label: "Rename sidecars",
              onChange: setValue,
            }),
            h(CheckField, {
              form,
              name: "calculate_md5",
              label: "Calculate MD5",
              onChange: setValue,
            })
          )
        ),
        h(
          Section,
          { title: "Generated Assets" },
          formGrid([
            h(SelectField, {
              key: "generated_strategy",
              form,
              name: "generated_strategy",
              label: "Strategy",
              options: SELECT_OPTIONS.generated_strategy,
              onChange: setValue,
            }),
          ]),
          h(
            "div",
            { className: "bulk-transcode-check-grid" },
            h(CheckField, {
              form,
              name: "generated_previews",
              label: "Previews",
              onChange: setValue,
              disabled: !generatedEnabled,
            }),
            h(CheckField, {
              form,
              name: "generated_sprites",
              label: "Sprites",
              onChange: setValue,
              disabled: !generatedEnabled,
            }),
            h(CheckField, {
              form,
              name: "generated_transcodes",
              label: "Generated transcodes",
              onChange: setValue,
              disabled: !generatedEnabled,
            }),
            h(CheckField, {
              form,
              name: "force_generated_transcodes",
              label: "Force regenerated transcodes",
              onChange: setValue,
              disabled: !regenerateEnabled || !form.generated_transcodes,
            })
          )
        )
      ),
      h(JobQueue, { reloadToken: queueReloadToken }),
      h(BulkTranscodeLog, null)
    );
  }

  PluginApi.register.route(ROUTE, BulkTranscodePage);

  PluginApi.patch.instead("PluginSettings", function () {
    const args = Array.prototype.slice.call(arguments);
    const next = args.pop();
    const props = args[0];

    if (!props || props.pluginID !== PLUGIN_ID) {
      return next.apply(this, args);
    }

    return h(BulkTranscodePluginSettings, { settings: props.settings });
  });

  PluginApi.patch.before("SettingsToolsSection", function (props) {
    const Setting = PluginApi.components.Setting;
    if (
      !Setting ||
      !containsPropValue(props.children, "href", "/playground")
    ) {
      return [props];
    }
    return [
      {
        children: h(
          React.Fragment,
          null,
          props.children,
          h(Setting, {
            heading: h(
              Link,
              { to: ROUTE },
              h(
                Button,
                null,
                h(FontAwesomeIcon, {
                  className: "fa-fw",
                  icon: Icons.faVideo,
                }),
                " Bulk Transcode"
              )
            ),
            subHeading:
              "Configure in-place media transcoding and monitor its queued jobs.",
          })
        ),
      },
    ];
  });
})();
