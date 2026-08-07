import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bulk_transcode


def base_settings(**overrides):
    values = {
        "target": "hevc",
        "hardware_encoder": "hevc_nvenc",
        "ffmpeg_path": "ffmpeg",
        "ffprobe_path": "ffprobe",
    }
    values.update(overrides)
    settings = bulk_transcode.normalize_settings(values)
    settings["_available_encoders"] = {settings["hardware_encoder"]}
    return settings


def assert_subsequence(testcase, sequence, expected):
    start = 0
    for value in expected:
        try:
            start = sequence.index(value, start) + 1
        except ValueError as exc:
            raise AssertionError(
                f"expected {expected!r} as subsequence of {sequence!r}"
            ) from exc


class ExtraArgsTests(unittest.TestCase):
    def test_extra_args_default_to_empty_strings(self):
        settings = bulk_transcode.normalize_settings({})

        self.assertEqual(settings["hevc_video_extra_args"], "")
        self.assertEqual(settings["av1_video_extra_args"], "")
        self.assertEqual(settings["audio_extra_args"], "")
        self.assertEqual(settings["target_size_percent"], 60)
        self.assertNotIn("_".join(["target", "savings", "percent"]), settings)
        self.assertTrue(settings["require_smaller_output"])

    def test_nvenc_hevc_does_not_add_default_preset(self):
        settings = base_settings(target="hevc", hardware_encoder="hevc_nvenc")

        args, _ = bulk_transcode.ffmpeg_args(
            Path("/media/source.mp4"),
            Path("/media/output.mp4"),
            settings,
            {"audio_streams": [], "subtitle_streams": []},
            "mp4",
        )

        self.assertNotIn("-preset", args)

    def test_nvenc_av1_preset_can_be_supplied_via_extra_args(self):
        settings = base_settings(
            target="av1",
            hardware_encoder="av1_nvenc",
            av1_video_extra_args="-preset p7",
        )

        args, _ = bulk_transcode.ffmpeg_args(
            Path("/media/source.mp4"),
            Path("/media/output.mp4"),
            settings,
            {"audio_streams": [], "subtitle_streams": []},
            "mp4",
        )

        self.assertEqual(args.count("-preset"), 1)
        assert_subsequence(self, args, ["-c:v", "av1_nvenc", "-preset", "p7"])

    def test_hevc_video_extra_args_are_appended_to_hevc_video_args(self):
        settings = base_settings(
            target="hevc",
            hardware_encoder="hevc_nvenc",
            hevc_video_extra_args="-spatial-aq 1 -aq-strength 8",
            av1_video_extra_args="-tiles 2x2",
        )

        args, _ = bulk_transcode.ffmpeg_args(
            Path("/media/source.mp4"),
            Path("/media/output.mp4"),
            settings,
            {"audio_streams": [], "subtitle_streams": []},
            "mp4",
        )

        assert_subsequence(
            self,
            args,
            ["-c:v", "hevc_nvenc", "-spatial-aq", "1", "-aq-strength", "8"],
        )
        self.assertNotIn("-tiles", args)

    def test_av1_video_extra_args_are_appended_to_av1_video_args(self):
        settings = base_settings(
            target="av1",
            hardware_encoder="av1_nvenc",
            av1_video_extra_args="-tiles 2x2 -multipass fullres",
            hevc_video_extra_args="-spatial-aq 1",
        )

        args, _ = bulk_transcode.ffmpeg_args(
            Path("/media/source.mp4"),
            Path("/media/output.mp4"),
            settings,
            {"audio_streams": [], "subtitle_streams": []},
            "mp4",
        )

        assert_subsequence(
            self,
            args,
            ["-c:v", "av1_nvenc", "-tiles", "2x2", "-multipass", "fullres"],
        )
        self.assertNotIn("-spatial-aq", args)

    def test_audio_extra_args_are_appended_after_generated_audio_args(self):
        settings = base_settings(audio_extra_args="-b:a 192k -ar 48000")

        args, _ = bulk_transcode.ffmpeg_args(
            Path("/media/source.mkv"),
            Path("/media/output.mkv"),
            settings,
            {"audio_streams": [{"codec_name": "aac"}], "subtitle_streams": []},
            "mkv",
        )

        assert_subsequence(self, args, ["-c:a:0", "copy", "-b:a", "192k", "-ar", "48000"])

    def test_ffmpeg_args_request_machine_readable_progress(self):
        settings = base_settings()

        args, _ = bulk_transcode.ffmpeg_args(
            Path("/media/source.mp4"),
            Path("/media/output.mp4"),
            settings,
            {"audio_streams": [], "subtitle_streams": []},
            "mp4",
        )

        assert_subsequence(self, args, ["-nostats", "-progress", "pipe:1"])

    def test_target_percent_sets_nvenc_rate_control_budget_without_cq(self):
        settings = base_settings(target_size_percent=60)

        args, _ = bulk_transcode.ffmpeg_args(
            Path("/media/source.mp4"),
            Path("/media/output.mp4"),
            settings,
            {"duration": 100, "audio_streams": [], "subtitle_streams": []},
            "mp4",
            source_size=100_000_000,
        )

        assert_subsequence(
            self,
            args,
            [
                "-c:v",
                "hevc_nvenc",
                "-rc",
                "vbr",
                "-b:v",
                "4800k",
                "-maxrate",
                "7200k",
                "-bufsize",
                "14400k",
            ],
        )
        self.assertNotIn("-cq:v", args)

    def test_target_percent_reserves_copied_audio_bitrate(self):
        settings = base_settings(target_size_percent=60)

        args, _ = bulk_transcode.ffmpeg_args(
            Path("/media/source.mp4"),
            Path("/media/output.mp4"),
            settings,
            {
                "duration": 100,
                "audio_streams": [{"codec_name": "aac", "bit_rate": "200000"}],
                "subtitle_streams": [],
            },
            "mp4",
            source_size=100_000_000,
        )

        assert_subsequence(
            self,
            args,
            ["-c:v", "hevc_nvenc", "-b:v", "4600k", "-maxrate", "6900k", "-bufsize", "13800k"],
        )

    def test_nvenc_cq_is_kept_when_target_size_is_disabled(self):
        settings = base_settings(target_size_percent=0)

        args, _ = bulk_transcode.ffmpeg_args(
            Path("/media/source.mp4"),
            Path("/media/output.mp4"),
            settings,
            {"duration": 100, "audio_streams": [], "subtitle_streams": []},
            "mp4",
            source_size=100_000_000,
        )

        assert_subsequence(self, args, ["-c:v", "hevc_nvenc", "-cq:v", "28", "-b:v", "0"])
        self.assertNotIn("-maxrate", args)
        self.assertNotIn("-bufsize", args)


class FfmpegProgressTests(unittest.TestCase):
    def test_progress_fraction_uses_output_time_and_duration(self):
        self.assertEqual(
            bulk_transcode.ffmpeg_progress_fraction({"out_time_us": "2500000"}, 10),
            0.25,
        )
        self.assertEqual(
            bulk_transcode.ffmpeg_progress_fraction({"out_time": "00:00:05.000000"}, 10),
            0.5,
        )

    def test_ffmpeg_runner_reports_progress_from_stdout(self):
        values = []
        script = (
            "import sys\n"
            "print('out_time_us=2500000', flush=True)\n"
            "print('progress=continue', flush=True)\n"
            "print('out_time_us=5000000', flush=True)\n"
            "print('progress=end', flush=True)\n"
        )

        returncode, stderr = bulk_transcode.run_ffmpeg_command(
            [sys.executable, "-c", script],
            duration=5,
            progress_callback=values.append,
        )

        self.assertEqual(returncode, 0)
        self.assertEqual(stderr, "")
        self.assertIn(0.5, values)
        self.assertEqual(values[-1], 1.0)


class DryRunTests(unittest.TestCase):
    def test_dry_run_logs_ffmpeg_command_without_executing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source_path = Path(tmpdir) / "source.mkv"
            source_path.write_bytes(b"not a real video")
            settings = base_settings(dry_run=True, skip_if_target=False)
            messages = []
            original_info = bulk_transcode.info
            original_probe_video = bulk_transcode.probe_video
            original_run_ffmpeg = bulk_transcode.run_ffmpeg
            try:
                bulk_transcode.info = messages.append
                bulk_transcode.probe_video = lambda _path, _settings: {
                    "video_codec": "h264",
                    "audio_streams": [],
                    "subtitle_streams": [],
                }
                bulk_transcode.run_ffmpeg = lambda *_args, **_kwargs: self.fail(
                    "dry run must not execute ffmpeg"
                )

                result, scene_ids = bulk_transcode.process_file(
                    None,
                    {
                        "id": "10",
                        "path": tmpdir,
                        "basename": source_path.name,
                        "scene_ids": "7,8",
                    },
                    settings,
                    {},
                )
            finally:
                bulk_transcode.info = original_info
                bulk_transcode.probe_video = original_probe_video
                bulk_transcode.run_ffmpeg = original_run_ffmpeg

        self.assertEqual(result, "dry_run")
        self.assertEqual(scene_ids, [7, 8])
        self.assertTrue(any("Would transcode" in message for message in messages))
        command_logs = [message for message in messages if "ffmpeg" in message and "-i" in message]
        self.assertEqual(len(command_logs), 1)
        self.assertIn(str(source_path), command_logs[0])


class TranscodeLogTests(unittest.TestCase):
    def test_success_log_includes_command_runtime_sizes_and_savings(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source_path = Path(tmpdir) / "source.mp4"
            source_path.write_bytes(b"x" * 100)
            settings = base_settings(skip_if_target=False, calculate_md5=False)
            messages = []

            originals = {
                "info": bulk_transcode.info,
                "probe_video": bulk_transcode.probe_video,
                "run_ffmpeg": bulk_transcode.run_ffmpeg,
                "oshash_file": bulk_transcode.oshash_file,
                "update_database": bulk_transcode.update_database,
            }

            def fake_probe_video(path, _settings):
                return {
                    "duration": 10,
                    "video_codec": "hevc" if path != source_path else "h264",
                    "format": "mp4",
                    "audio_streams": [],
                    "subtitle_streams": [],
                }

            def fake_run_ffmpeg(_source_path, temp_path, *_args, **_kwargs):
                temp_path.write_bytes(b"y" * 60)

            try:
                bulk_transcode.info = messages.append
                bulk_transcode.probe_video = fake_probe_video
                bulk_transcode.run_ffmpeg = fake_run_ffmpeg
                bulk_transcode.oshash_file = lambda _path: "oshash"
                bulk_transcode.update_database = lambda *_args: None

                result, _scene_ids = bulk_transcode.process_file(
                    None,
                    {"id": "10", "path": tmpdir, "basename": source_path.name},
                    settings,
                    {},
                )
            finally:
                for name, value in originals.items():
                    setattr(bulk_transcode, name, value)

        self.assertEqual(result, "transcoded")
        start_logs = [message for message in messages if "Starting transcode" in message]
        finish_logs = [message for message in messages if "Finished transcode" in message]
        self.assertEqual(len(start_logs), 1)
        self.assertIn("ffmpeg", start_logs[0])
        self.assertIn("-progress pipe:1", start_logs[0])
        self.assertEqual(len(finish_logs), 1)
        self.assertIn("success", finish_logs[0])
        self.assertIn("in ", finish_logs[0])
        self.assertIn("100 B -> 60 B", finish_logs[0])
        self.assertIn("savings 40.0%", finish_logs[0])

    def test_failure_log_includes_runtime_and_outcome(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source_path = Path(tmpdir) / "source.mp4"
            source_path.write_bytes(b"x" * 100)
            settings = base_settings(skip_if_target=False)
            messages = []

            originals = {
                "info": bulk_transcode.info,
                "probe_video": bulk_transcode.probe_video,
                "run_ffmpeg": bulk_transcode.run_ffmpeg,
            }

            def fake_probe_video(_path, _settings):
                return {
                    "duration": 10,
                    "video_codec": "h264",
                    "format": "mp4",
                    "audio_streams": [],
                    "subtitle_streams": [],
                }

            try:
                bulk_transcode.info = messages.append
                bulk_transcode.probe_video = fake_probe_video
                bulk_transcode.run_ffmpeg = lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    RuntimeError("encoder failed")
                )

                with self.assertRaises(RuntimeError):
                    bulk_transcode.process_file(
                        None,
                        {"id": "10", "path": tmpdir, "basename": source_path.name},
                        settings,
                        {},
                    )
            finally:
                for name, value in originals.items():
                    setattr(bulk_transcode, name, value)

        finish_logs = [message for message in messages if "Finished transcode" in message]
        self.assertEqual(len(finish_logs), 1)
        self.assertIn("failed", finish_logs[0])
        self.assertIn("in ", finish_logs[0])
        self.assertIn("encoder failed", finish_logs[0])

    def test_larger_output_is_discarded_without_replacing_source(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source_path = Path(tmpdir) / "source.mp4"
            source_path.write_bytes(b"x" * 100)
            settings = base_settings(
                skip_if_target=False,
                calculate_md5=False,
                require_smaller_output=True,
            )
            messages = []

            originals = {
                "info": bulk_transcode.info,
                "probe_video": bulk_transcode.probe_video,
                "run_ffmpeg": bulk_transcode.run_ffmpeg,
                "update_database": bulk_transcode.update_database,
            }

            def fake_probe_video(path, _settings):
                return {
                    "duration": 10,
                    "video_codec": "hevc" if path != source_path else "h264",
                    "format": "mp4",
                    "audio_streams": [],
                    "subtitle_streams": [],
                }

            def fake_run_ffmpeg(_source_path, temp_path, *_args, **_kwargs):
                temp_path.write_bytes(b"y" * 120)

            try:
                bulk_transcode.info = messages.append
                bulk_transcode.probe_video = fake_probe_video
                bulk_transcode.run_ffmpeg = fake_run_ffmpeg
                bulk_transcode.update_database = lambda *_args: self.fail(
                    "discarded output must not update the database"
                )

                result, _scene_ids = bulk_transcode.process_file(
                    None,
                    {"id": "10", "path": tmpdir, "basename": source_path.name},
                    settings,
                    {},
                )
                source_bytes = source_path.read_bytes()
            finally:
                for name, value in originals.items():
                    setattr(bulk_transcode, name, value)

        self.assertEqual(result, "skipped")
        self.assertEqual(source_bytes, b"x" * 100)
        finish_logs = [message for message in messages if "Finished transcode" in message]
        self.assertEqual(len(finish_logs), 1)
        self.assertIn("discarded", finish_logs[0])
        self.assertIn("100 B -> 120 B", finish_logs[0])
        self.assertIn("savings -20.0%", finish_logs[0])


class RunProgressTests(unittest.TestCase):
    def test_run_maps_file_progress_to_overall_progress(self):
        values = []
        original_client = bulk_transcode.StashClient
        original_find_video_files = bulk_transcode.find_video_files
        original_process_file = bulk_transcode.process_file
        original_progress = bulk_transcode.progress
        original_info = bulk_transcode.info

        class FakeClient:
            def __init__(self, _connection):
                pass

            def configuration(self):
                return {"general": {}, "plugins": {}}

        def fake_process_file(_client, row, _settings, _general, progress_callback=None):
            self.assertIsNotNone(progress_callback)
            progress_callback(0.5)
            return ("transcoded" if row["id"] == "1" else "skipped"), []

        try:
            bulk_transcode.StashClient = FakeClient
            bulk_transcode.find_video_files = lambda _client, _settings: [
                {"id": "1"},
                {"id": "2"},
            ]
            bulk_transcode.process_file = fake_process_file
            bulk_transcode.progress = values.append
            bulk_transcode.info = lambda _message: None

            result = bulk_transcode.run({"server_connection": {}, "args": {}})
        finally:
            bulk_transcode.StashClient = original_client
            bulk_transcode.find_video_files = original_find_video_files
            bulk_transcode.process_file = original_process_file
            bulk_transcode.progress = original_progress
            bulk_transcode.info = original_info

        self.assertEqual(result["processed"], 2)
        self.assertEqual(result["transcoded"], 1)
        self.assertIn(0.25, values)
        self.assertIn(0.75, values)
        self.assertEqual(values[-1], 1.0)


if __name__ == "__main__":
    unittest.main()
