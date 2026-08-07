ALTER TABLE `video_captions` RENAME TO `video_captions_old`;

CREATE TABLE `video_captions` (
  `file_id` integer NOT NULL,
  `language_code` varchar(255) NOT NULL,
  `filename` varchar(255) NOT NULL,
  `caption_type` varchar(255) NOT NULL,
  `stream_index` integer NOT NULL DEFAULT -1,
  `title` varchar(255) NOT NULL DEFAULT '',
  primary key (`file_id`, `language_code`, `caption_type`, `stream_index`),
  foreign key(`file_id`) references `video_files`(`file_id`) on delete CASCADE
);

INSERT INTO `video_captions` (`file_id`, `language_code`, `filename`, `caption_type`)
SELECT `file_id`, `language_code`, `filename`, `caption_type`
FROM `video_captions_old`;

DROP TABLE `video_captions_old`;
