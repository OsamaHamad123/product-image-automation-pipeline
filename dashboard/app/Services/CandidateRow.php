<?php

namespace App\Services;

/**
 * Optional curation_candidates columns for one candidate posted by the dashboard
 * (save-candidates). Keeps the provenance the Python writer stores too: page_url, the
 * identity tier (which the bridge sends inside evidence.tier), the provider and query id
 * that found the image, and its pHash.
 *
 * Pure PHP on plain arrays (no framework calls) so it can be tested from the CLI.
 */
final class CandidateRow
{
    public static function optionalColumns(array $c, string $skuKey, string $runId): array
    {
        $evidence = (isset($c['evidence']) && is_array($c['evidence'])) ? $c['evidence'] : [];
        $tier = $c['identity_tier'] ?? ($evidence['tier'] ?? null);
        $pageUrl = trim((string) ($c['page_url'] ?? ($evidence['page_url'] ?? '')));
        $short = function ($value, int $limit) {
            $text = trim((string) ($value ?? ''));
            return $text !== '' ? mb_substr($text, 0, $limit) : null;
        };

        return [
            'sku_key' => $skuKey !== '' ? $skuKey : null,
            'run_id' => $runId,
            'reasons_json' => json_encode($c['reasons'] ?? [], JSON_UNESCAPED_UNICODE),
            'evidence_json' => json_encode($c['evidence'] ?? new \stdClass(), JSON_UNESCAPED_UNICODE),
            'vlm_json' => isset($c['vlm']) ? json_encode($c['vlm'], JSON_UNESCAPED_UNICODE) : null,
            'identity_tier' => is_numeric($tier) ? (int) $tier : null,
            'content_sha256' => isset($c['content_sha256']) ? mb_substr((string) $c['content_sha256'], 0, 64) : null,
            'page_url' => $pageUrl !== '' ? mb_substr($pageUrl, 0, 2048) : null,
            'provider' => $short($c['provider'] ?? null, 32),
            'query_id' => $short($c['query_id'] ?? null, 16),
            'phash' => $short($c['phash'] ?? null, 32),
        ];
    }
}
