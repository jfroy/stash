package migrations

import (
	"context"
	"fmt"

	"github.com/jmoiron/sqlx"
	"github.com/stashapp/stash/pkg/logger"
	"github.com/stashapp/stash/pkg/sqlite"
)

// Schema 87 adds embedded caption support (stream_index and title columns on
// video_captions).
//
// The cudajellyfin fork previously shipped this change as schema 86, which
// upstream later used for scene production dates. Databases created by the
// fork are therefore at schema 86 with the caption columns but without the
// production date columns, while upstream databases at schema 86 have the
// opposite. This migration inspects the schema and applies whichever
// changes are missing, so both kinds of database converge.
type schema87Migrator struct {
	migrator
}

func post87(ctx context.Context, db *sqlx.DB) error {
	logger.Info("Running post-migration for schema version 87")

	m := schema87Migrator{
		migrator: migrator{
			db: db,
		},
	}

	if err := m.ensureSceneProductionDate(ctx); err != nil {
		return fmt.Errorf("ensuring scene production date columns: %w", err)
	}

	if err := m.ensureEmbeddedCaptions(ctx); err != nil {
		return fmt.Errorf("ensuring embedded caption columns: %w", err)
	}

	return nil
}

func (m *schema87Migrator) hasColumn(ctx context.Context, table, column string) (bool, error) {
	var count int
	query := "SELECT COUNT(*) FROM pragma_table_info(?) WHERE name = ?"
	if err := m.db.GetContext(ctx, &count, query, table, column); err != nil {
		return false, fmt.Errorf("checking for column %s.%s: %w", table, column, err)
	}

	return count > 0, nil
}

func (m *schema87Migrator) ensureSceneProductionDate(ctx context.Context) error {
	has, err := m.hasColumn(ctx, "scenes", "production_date")
	if err != nil {
		return err
	}
	if has {
		return nil
	}

	logger.Info("Adding missing scene production date columns")

	return m.execAll([]string{
		"ALTER TABLE `scenes` ADD COLUMN `production_date` date",
		"ALTER TABLE `scenes` ADD COLUMN `production_date_precision` TINYINT",
	})
}

func (m *schema87Migrator) ensureEmbeddedCaptions(ctx context.Context) error {
	has, err := m.hasColumn(ctx, "video_captions", "stream_index")
	if err != nil {
		return err
	}
	if has {
		return nil
	}

	logger.Info("Adding embedded caption columns to video_captions")

	return m.withTxn(ctx, func(tx *sqlx.Tx) error {
		stmts := []string{
			"ALTER TABLE `video_captions` RENAME TO `video_captions_old`",
			`CREATE TABLE ` + "`video_captions`" + ` (
  ` + "`file_id`" + ` integer NOT NULL,
  ` + "`language_code`" + ` varchar(255) NOT NULL,
  ` + "`filename`" + ` varchar(255) NOT NULL,
  ` + "`caption_type`" + ` varchar(255) NOT NULL,
  ` + "`stream_index`" + ` integer NOT NULL DEFAULT -1,
  ` + "`title`" + ` varchar(255) NOT NULL DEFAULT '',
  primary key (` + "`file_id`, `language_code`, `caption_type`, `stream_index`" + `),
  foreign key(` + "`file_id`" + `) references ` + "`video_files`(`file_id`)" + ` on delete CASCADE
)`,
			"INSERT INTO `video_captions` (`file_id`, `language_code`, `filename`, `caption_type`) " +
				"SELECT `file_id`, `language_code`, `filename`, `caption_type` FROM `video_captions_old`",
			"DROP TABLE `video_captions_old`",
		}

		for _, stmt := range stmts {
			if _, err := tx.ExecContext(ctx, stmt); err != nil {
				return fmt.Errorf("executing statement %s: %w", stmt, err)
			}
		}

		return nil
	})
}

func init() {
	sqlite.RegisterPostMigration(87, post87)
}
