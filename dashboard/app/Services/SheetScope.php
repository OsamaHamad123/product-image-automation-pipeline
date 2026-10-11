<?php

namespace App\Services;

use App\Http\Controllers\ProductController;
use Illuminate\Support\Facades\Schema;

/**
 * Which Google Sheet a row of the review tables belongs to. The four tables keyed by row number or barcode
 * (curation_candidates, automation_queue, product_failures, sheet_updates) carry sheet_key =
 * "<spreadsheet_id>:<worksheet_gid>" (written by the Python side, which adds the column and backfills it). After the
 * owner switches the sheet or its tab, row 5 of the new sheet must not get the old sheet's candidates, queue state or
 * failure.
 *
 * The current key is the one the last sheet read returned (ProductController::currentSheetKey, never a bridge call).
 * Fallback, so the dashboard works before Python migrates and before the first read: when the column is missing or
 * the key is unknown, nothing is filtered and nothing is stamped (the behaviour before sheet_key).
 */
final class SheetScope
{
    public const COLUMN = 'sheet_key';

    public const TABLES = ['curation_candidates', 'automation_queue', 'product_failures', 'sheet_updates'];

    /** Schema::hasColumn per table, once per request (a PHP-FPM request starts with an empty static). */
    private static array $columns = [];

    public static function hasColumn(string $table): bool
    {
        if (!array_key_exists($table, self::$columns)) {
            try {
                self::$columns[$table] = Schema::hasColumn($table, self::COLUMN);
            } catch (\Throwable $e) {
                self::$columns[$table] = false;     // no database or no table: nothing to filter
            }
        }
        return self::$columns[$table];
    }

    /** The key to filter (or stamp) $table with, or null: the column is missing or the current sheet is unknown. */
    public static function key(string $table): ?string
    {
        $key = ProductController::currentSheetKey();
        return ($key !== null && self::hasColumn($table)) ? $key : null;
    }

    /** $query limited to the current sheet's rows of $table (unchanged in the fallback). $column may be qualified. */
    public static function apply($query, string $table, string $column = self::COLUMN)
    {
        $key = self::key($table);
        if ($key !== null) {
            $query->where($column, $key);
        }
        return $query;
    }

    /** " AND sheet_key = ?" for raw SQL on $table (its binding appended to $bindings), or '' in the fallback. */
    public static function sql(string $table, array &$bindings, string $column = self::COLUMN): string
    {
        $key = self::key($table);
        if ($key === null) {
            return '';
        }
        $bindings[] = $key;
        return ' AND ' . $column . ' = ?';
    }

    /** ['sheet_key' => key] for a row written into $table, or [] (the column stays NULL) in the fallback. */
    public static function stamp(string $table): array
    {
        $key = self::key($table);
        return $key !== null ? [self::COLUMN => $key] : [];
    }

    /** Forget the column checks (a test, or after a migration in the same process). */
    public static function forgetColumns(): void
    {
        self::$columns = [];
    }
}
