<?php

namespace App\Services;

use Illuminate\Support\Facades\Log;

/**
 * Single client for the Python CLI bridge (cli_bridge.py).
 *
 * Every dashboard action runs `python cli_bridge.py <action> <base64 JSON>` directly.
 * There is no HTTP attempt first: FastAPI is not launched, and a second transport
 * with different behaviour is what let bugs hide before (decision D12).
 */
class PythonBridge
{
    /** Seconds a single bridge call may keep the PHP request alive. */
    public const TIME_LIMIT = 600;

    /** Statuses that describe a completed action (not a transport or code error). */
    public const OUTCOME_STATUSES = ['success', 'review', 'not_found'];

    /** The interpreter: an explicitly set PYTHON_PATH, else the app's .venv (Windows or Linux layout), else python. */
    public static function pythonPath(): string
    {
        $explicit = trim((string) env('PYTHON_PATH', ''));
        if ($explicit !== '') {
            return $explicit;
        }
        $winVenv = base_path('../.venv/Scripts/python.exe');
        if (file_exists($winVenv)) {
            return $winVenv;
        }
        $linuxVenv = base_path('../.venv/bin/python');
        if (file_exists($linuxVenv)) {
            return $linuxVenv;
        }
        return 'python';
    }

    public static function bridgePath(): string
    {
        return env('CLI_BRIDGE_PATH', base_path('../cli_bridge.py'));
    }

    /**
     * Build the shell command. The action name is restricted to [a-z_-] and the
     * parameters travel as base64, so nothing user-controlled reaches the shell.
     */
    public static function buildCommand(string $action, array $params = []): string
    {
        if (!preg_match('/^[a-z][a-z_-]*$/', $action)) {
            throw new \InvalidArgumentException("Invalid bridge action: {$action}");
        }
        // Always a JSON object: an empty PHP array would otherwise encode as [] and
        // break params.get(...) in the bridge.
        $document = empty($params) ? new \stdClass() : $params;
        $payload = base64_encode(json_encode($document, JSON_UNESCAPED_UNICODE | JSON_INVALID_UTF8_SUBSTITUTE));
        $python = self::pythonPath();
        $bridge = self::bridgePath();
        return "\"{$python}\" \"{$bridge}\" {$action} {$payload} 2>&1";
    }

    /**
     * Run one bridge action and return its decoded JSON result.
     * On a transport or decode failure the result is {status: 'error', error: ...}.
     */
    public static function run(string $action, array $params = []): array
    {
        // UTF-8 for the child process: Arabic names and log text must never crash a
        // Windows console code page (cp1252 / cp1256).
        putenv('PYTHONUTF8=1');
        putenv('PYTHONIOENCODING=utf-8');

        $limit = (int) ini_get('max_execution_time');
        if ($limit !== 0 && $limit < self::TIME_LIMIT) {
            @set_time_limit(self::TIME_LIMIT);
        }

        try {
            $output = shell_exec(self::buildCommand($action, $params));
        } catch (\Throwable $e) {
            Log::error("Python bridge failed to start for action {$action}: " . $e->getMessage());
            return ['status' => 'error', 'error' => 'Python bridge failed to start: ' . $e->getMessage()];
        }

        $decoded = self::decodeOutput(is_string($output) ? $output : null);
        if ($decoded === null) {
            $raw = is_string($output) ? mb_substr($output, -4000) : '';
            Log::error("Python bridge returned no JSON for action {$action}. Raw tail: " . $raw);
            return ['status' => 'error', 'error' => 'Invalid JSON output from Python bridge', 'raw' => $raw];
        }
        if (self::isError($decoded)) {
            Log::warning("Python bridge action {$action} returned status " . ($decoded['status'] ?? 'missing'));
        }
        return $decoded;
    }

    /**
     * Extract the result document from the bridge output. stdout should carry exactly
     * one JSON document; stray lines before it (warnings, tracebacks) are tolerated.
     */
    public static function decodeOutput(?string $output): ?array
    {
        if ($output === null) {
            return null;
        }
        $trimmed = trim($output);
        if ($trimmed === '') {
            return null;
        }
        $decoded = json_decode($trimmed, true);
        if (is_array($decoded)) {
            return $decoded;
        }
        $lines = preg_split('/\r?\n/', $trimmed);
        for ($i = count($lines) - 1; $i >= 0; $i--) {
            $line = trim($lines[$i]);
            if ($line !== '' && $line[0] === '{') {
                $decoded = json_decode($line, true);
                if (is_array($decoded)) {
                    return $decoded;
                }
            }
        }
        $pos = strrpos($trimmed, '{"status"');
        if ($pos !== false) {
            $decoded = json_decode(substr($trimmed, $pos), true);
            if (is_array($decoded)) {
                return $decoded;
            }
        }
        return null;
    }

    public static function isError(array $result): bool
    {
        $status = $result['status'] ?? 'error';
        return !in_array($status, self::OUTCOME_STATUSES, true) && $status !== 'provider_down';
    }

    /**
     * HTTP status for a bridge result: completed outcomes are 200 (the UI renders
     * candidates for review and not_found too), provider_down is a retryable 503,
     * anything else is a 500.
     */
    public static function httpStatus(array $result): int
    {
        $status = $result['status'] ?? 'error';
        if (in_array($status, self::OUTCOME_STATUSES, true)) {
            return 200;
        }
        if ($status === 'provider_down') {
            return 503;
        }
        return 500;
    }
}
