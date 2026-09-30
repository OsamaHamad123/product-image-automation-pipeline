<?php

namespace App\Services;

/**
 * Attaches stored curation candidates to sheet products.
 *
 * Candidates are keyed by sku_key (GTIN-14 when the barcode is valid, otherwise the
 * Python identity hash). Row numbers shift when the sheet is edited, so row_number is
 * only used for legacy candidates whose sku_key is NULL. Only the latest run for a
 * sku_key is shown, and nothing is pre-selected unless the pipeline marked it.
 *
 * Pure PHP on plain arrays (no framework calls) so it can be tested from the CLI.
 */
final class CandidateMatcher
{
    /** Fields that must never reach the UI: clip_score held keyword points shown as "% Match". */
    private const HIDDEN_FIELDS = ['clip_score', 'reasons_json', 'evidence_json', 'vlm_json'];

    /**
     * GS1 GTIN-8/12/13/14 validation; returns the zero-padded GTIN-14 or null.
     * Mirrors catalog_match.gtin.normalize_gtin for the common sheet spellings.
     */
    public static function gtin14($raw): ?string
    {
        if ($raw === null || is_bool($raw) || is_float($raw)) {
            return null;
        }
        $s = trim((string) $raw);
        $s = strtr($s, [
            '٠' => '0', '١' => '1', '٢' => '2', '٣' => '3', '٤' => '4',
            '٥' => '5', '٦' => '6', '٧' => '7', '٨' => '8', '٩' => '9',
            '۰' => '0', '۱' => '1', '۲' => '2', '۳' => '3', '۴' => '4',
            '۵' => '5', '۶' => '6', '۷' => '7', '۸' => '8', '۹' => '9',
        ]);
        if (strpos($s, "'") === 0) {
            $s = trim(substr($s, 1));
        }
        if ($s === '') {
            return null;
        }
        if (preg_match('/^\d{1,3}(?:,\d{3}){2,}$/', $s)) {
            // '6,297,000,611,365' keeps every digit
            $s = str_replace(',', '', $s);
        }
        if (preg_match('/\d\s*[eE]\s*[+-]?\s*\d/', $s) || preg_match('/\d[.,]\d/', $s)) {
            // scientific notation or a decimal part: digits were lost, never guess
            return null;
        }
        $digits = preg_replace('/[\s\-]+/u', '', $s);
        if ($digits === null || $digits === '' || !ctype_digit($digits)) {
            return null;
        }
        $len = strlen($digits);
        if (!in_array($len, [8, 12, 13, 14], true) || trim($digits, '0') === '') {
            return null;
        }
        $body = substr($digits, 0, -1);
        $total = 0;
        $reversed = strrev($body);
        for ($i = 0, $n = strlen($reversed); $i < $n; $i++) {
            $total += ((int) $reversed[$i]) * ($i % 2 === 0 ? 3 : 1);
        }
        $check = (10 - $total % 10) % 10;
        if ($check !== (int) substr($digits, -1)) {
            return null;
        }
        return str_pad($digits, 14, '0', STR_PAD_LEFT);
    }

    private static function norm($value): string
    {
        return mb_strtolower(trim(preg_replace('/\s+/u', ' ', (string) $value)));
    }

    private static function hasKey($value): bool
    {
        return $value !== null && trim((string) $value) !== '';
    }

    /**
     * The product's sku_key: the one Python sent with the product, else its GTIN-14,
     * else the key stored on candidates written for this row under the same product name.
     */
    public static function productSkuKey(array $product, array $keyedRowsAtRow = []): ?string
    {
        if (self::hasKey($product['sku_key'] ?? null)) {
            return trim((string) $product['sku_key']);
        }
        $gtin = self::gtin14($product['barcode'] ?? null);
        if ($gtin !== null) {
            return $gtin;
        }
        $name = self::norm($product['product_name'] ?? '');
        if ($name === '') {
            return null;
        }
        $keys = [];
        foreach ($keyedRowsAtRow as $row) {
            if (self::norm($row['product_name'] ?? '') === $name && self::hasKey($row['sku_key'] ?? null)) {
                $keys[trim((string) $row['sku_key'])] = true;
            }
        }
        return count($keys) === 1 ? array_key_first($keys) : null;
    }

    /** Keyed rows at the product's row whose product name matches and that share one sku_key. */
    private static function sameNameRows(array $product, array $keyedRowsAtRow): array
    {
        $name = self::norm($product['product_name'] ?? '');
        if ($name === '') {
            return [];
        }
        $matches = array_values(array_filter($keyedRowsAtRow,
            fn ($r) => self::norm($r['product_name'] ?? '') === $name));
        $keys = array_unique(array_map(fn ($r) => trim((string) ($r['sku_key'] ?? '')), $matches));
        return count($keys) === 1 ? $matches : [];
    }

    /** Keep only the rows of the most recent run (the run_id of the highest id). */
    public static function latestRun(array $rows): array
    {
        if (empty($rows)) {
            return [];
        }
        usort($rows, fn ($a, $b) => ((int) ($a['id'] ?? 0)) <=> ((int) ($b['id'] ?? 0)));
        $last = end($rows);
        $latest = self::hasKey($last['run_id'] ?? null) ? (string) $last['run_id'] : null;
        return array_values(array_filter($rows, function ($r) use ($latest) {
            $run = self::hasKey($r['run_id'] ?? null) ? (string) $r['run_id'] : null;
            return $run === $latest;
        }));
    }

    public static function decodeJson($value, $default)
    {
        if (is_array($value)) {
            return $value;
        }
        if (!is_string($value) || trim($value) === '') {
            return $default;
        }
        $decoded = json_decode($value, true);
        return $decoded ?? $default;
    }

    /** Candidate as the UI sees it: decoded status/reasons/evidence/vlm, no fabricated scores. */
    public static function present(array $row): array
    {
        $out = $row;
        $out['status'] = (string) ($row['status'] ?? 'pending');
        $out['reasons'] = self::decodeJson($row['reasons_json'] ?? null, []);
        $out['evidence'] = self::decodeJson($row['evidence_json'] ?? null, (object) []);
        $out['vlm'] = self::decodeJson($row['vlm_json'] ?? null, null);
        $out['is_selected'] = (int) ($row['is_selected'] ?? 0);
        foreach (self::HIDDEN_FIELDS as $field) {
            unset($out[$field]);
        }
        return $out;
    }

    /**
     * Attach candidates to products and set needs_review / needs_review_url / preselected.
     * needs_review_url is only ever the candidate the pipeline (or a reviewer) selected;
     * it never falls back to the first candidate.
     */
    public static function attach(array $products, array $candidateRows): array
    {
        $bySku = [];
        $legacyByRow = [];
        $keyedByRow = [];
        foreach ($candidateRows as $row) {
            $row = (array) $row;
            if (self::hasKey($row['sku_key'] ?? null)) {
                $bySku[trim((string) $row['sku_key'])][] = $row;
                $keyedByRow[(int) ($row['row_number'] ?? 0)][] = $row;
            } else {
                $legacyByRow[(int) ($row['row_number'] ?? 0)][] = $row;
            }
        }

        foreach ($products as &$prod) {
            $rowNum = (int) ($prod['row_number'] ?? 0);
            $key = self::productSkuKey($prod, $keyedByRow[$rowNum] ?? []);
            if ($key !== null) {
                $prod['sku_key'] = $key;
            }

            if ($key !== null && !empty($bySku[$key])) {
                $rows = self::latestRun($bySku[$key]);
            } else {
                // No rows under this key (the key was computed differently when the worker stored
                // them): fall back to keyed rows at the same row with the same product name, then
                // to legacy rows. An empty list would silently hide pre-cached candidates.
                $rows = self::latestRun(self::sameNameRows($prod, $keyedByRow[$rowNum] ?? []));
                if (empty($rows)) {
                    $rows = self::latestRun($legacyByRow[$rowNum] ?? []);
                }
            }
            $candidates = array_map([self::class, 'present'], $rows);
            $prod['curation_candidates'] = $candidates;

            if (!empty($candidates)) {
                $selected = null;
                foreach ($candidates as $c) {
                    if ($c['is_selected'] === 1) {
                        $selected = $c;
                        break;
                    }
                }
                $prod['needs_review'] = true;
                $prod['needs_review_url'] = $selected['image_url'] ?? '';
                $prod['preselected'] = $selected !== null;
            } elseif (strpos((string) ($prod['existing_image_link'] ?? ''), 'needs_review:') !== false) {
                $prod['needs_review'] = true;
                $prod['needs_review_url'] = trim(str_replace('needs_review:', '', (string) $prod['existing_image_link']));
                $prod['preselected'] = false;
            }
        }
        unset($prod);
        return $products;
    }
}
