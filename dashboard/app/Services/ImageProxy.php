<?php

namespace App\Services;

use GuzzleHttp\Psr7\Uri;
use GuzzleHttp\Psr7\UriResolver;
use Illuminate\Http\Client\Response;
use Illuminate\Support\Facades\Cache;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Http;

/**
 * The fetch behind /api/image-proxy (ApiController::imageProxy): the review screen shows store images through the
 * dashboard because many stores block hotlinking. The proxy was an open fetcher (any http(s) URL, TLS unchecked,
 * redirects followed, the whole body in memory); now it fetches only:
 *
 * - from a host the system itself has handed to the review screen (allowedHost): Cloudinary (our own images), a host
 *   of a stored candidate, rejected image or approved source (curation_candidates, rejected_images,
 *   resolved_products, review_decisions), or one remembered when the dashboard relayed a search result or the
 *   Sheet's image links (rememberHosts, kept HOSTS_TTL_S);
 * - over verified TLS, from public addresses only: the host and every redirect hop are resolved and refused when any
 *   address is loopback, private (RFC 1918), link-local (169.254.169.254 included), CGNAT, multicast, reserved, IPv6
 *   ULA / link-local or an IPv4-mapped / 6to4 / NAT64 form of these; curl is pinned to the checked addresses;
 * - at most MAX_BYTES, read in chunks (a larger body is refused without being held in memory).
 *
 * The same rules as the pipeline's net_guard.py. Errors carry a code, never an exception's text.
 */
class ImageProxy
{
    public const MAX_BYTES = 15 * 1024 * 1024;
    public const MAX_REDIRECTS = 5;
    public const TIMEOUT_S = 10;
    public const HOSTS_CACHE_KEY = 'image_proxy_hosts';
    public const HOSTS_TTL_S = 14 * 86400;
    private const HOSTS_MAX = 5000;
    private const DB_HIT_TTL_S = 600;
    private const DB_MISS_TTL_S = 30;
    private const DNS_TTL_S = 60;

    /** Tables and columns whose image URLs the review screen shows (their hosts may be fetched). */
    private const URL_COLUMNS = [
        ['curation_candidates', 'image_url'], ['rejected_images', 'original_url'],
        ['resolved_products', 'original_url'], ['review_decisions', 'image_url'],
    ];

    private const BLOCKED_NETS = [
        '0.0.0.0/8', '10.0.0.0/8', '100.64.0.0/10', '127.0.0.0/8', '169.254.0.0/16', '172.16.0.0/12', '192.0.0.0/24',
        '192.0.2.0/24', '192.168.0.0/16', '198.18.0.0/15', '198.51.100.0/24', '203.0.113.0/24', '224.0.0.0/4',
        '240.0.0.0/4', '::/96', '100::/64', '2001::/32', '2001:db8::/32', 'fc00::/7', 'fe80::/10', 'fec0::/10', 'ff00::/8',
    ];

    /** @var (callable(string): array<string>)|null DNS for tests: host -> its addresses */
    public static $resolver = null;

    // ------------------------------------------------------------------
    // Hosts
    // ------------------------------------------------------------------

    public static function hostOf(mixed $url): string
    {
        if (!is_string($url) || $url === '') {
            return '';
        }
        $parts = parse_url(trim($url));
        if (!is_array($parts) || !in_array(strtolower((string) ($parts['scheme'] ?? '')), ['http', 'https'], true)) {
            return '';
        }
        return rtrim(strtolower((string) ($parts['host'] ?? '')), '.');
    }

    /** Remember the hosts of image URLs the dashboard just handed to the review screen (a search result, Sheet links). */
    public static function rememberHosts(iterable $urls): void
    {
        $hosts = [];
        foreach ($urls as $url) {
            $host = self::hostOf($url);
            if ($host !== '') {
                $hosts[$host] = true;
            }
        }
        if (!$hosts) {
            return;
        }
        try {
            $known = Cache::get(self::HOSTS_CACHE_KEY, []);
            $known = is_array($known) ? $known : [];
            $now = time();
            $changed = false;
            foreach (array_keys($hosts) as $host) {
                if (($known[$host] ?? 0) < $now + intdiv(self::HOSTS_TTL_S, 2)) {
                    $known[$host] = $now + self::HOSTS_TTL_S;
                    $changed = true;
                }
            }
            if (!$changed) {
                return;
            }
            $known = array_filter($known, fn ($until) => $until > $now);
            if (count($known) > self::HOSTS_MAX) {
                arsort($known);
                $known = array_slice($known, 0, self::HOSTS_MAX, true);
            }
            Cache::put(self::HOSTS_CACHE_KEY, $known, self::HOSTS_TTL_S);
        } catch (\Throwable $e) {
            // a cache that cannot be written only means fewer images through the proxy
        }
    }

    /** May the proxy fetch from this host? (see the class comment) */
    public static function allowedHost(string $host): bool
    {
        $host = rtrim(strtolower(trim($host)), '.');
        if ($host === '') {
            return false;
        }
        if (preg_match('/^res(?:-\d+)?\.cloudinary\.com$/D', $host)) {
            return true;
        }
        try {
            $known = Cache::get(self::HOSTS_CACHE_KEY, []);
            if (is_array($known) && ($known[$host] ?? 0) > time()) {
                return true;
            }
            $key = 'image_proxy_host:' . sha1($host);
            $hit = Cache::get($key);
            if ($hit !== null) {
                return (int) $hit === 1;
            }
        } catch (\Throwable $e) {
            $key = null;
        }
        $found = self::hostInDatabase($host);
        if ($key !== null) {
            try {
                Cache::put($key, $found ? 1 : 0, $found ? self::DB_HIT_TTL_S : self::DB_MISS_TTL_S);
            } catch (\Throwable $e) {
                // not cached: asked again next time
            }
        }
        return $found;
    }

    private static function hostInDatabase(string $host): bool
    {
        $like = addcslashes($host, '%_\\');
        $patterns = ["http://{$like}/%", "https://{$like}/%", "http://{$like}:%", "https://{$like}:%",
                     "http://{$like}?%", "https://{$like}?%"];
        foreach (self::URL_COLUMNS as [$table, $column]) {
            try {
                $exists = DB::table($table)->where(function ($q) use ($column, $patterns) {
                    foreach ($patterns as $pattern) {
                        $q->orWhere($column, 'like', $pattern);
                    }
                })->exists();
                if ($exists) {
                    return true;
                }
            } catch (\Throwable $e) {
                // a table that does not exist yet holds no host
            }
        }
        return false;
    }

    // ------------------------------------------------------------------
    // Addresses
    // ------------------------------------------------------------------

    public static function isPublicIp(string $ip): bool
    {
        $packed = @inet_pton(explode('%', $ip, 2)[0]);
        if ($packed === false) {
            return false;
        }
        if (strlen($packed) === 16) {
            $embedded = null;
            if (strncmp($packed, str_repeat("\0", 10) . "\xff\xff", 12) === 0) {
                $embedded = substr($packed, 12, 4);                               // ::ffff:a.b.c.d
            } elseif (strncmp($packed, "\x20\x02", 2) === 0) {
                $embedded = substr($packed, 2, 4);                                // 6to4 2002::/16
            } elseif (strncmp($packed, "\x00\x64\xff\x9b" . str_repeat("\0", 8), 12) === 0) {
                $embedded = substr($packed, 12, 4);                               // NAT64 64:ff9b::/96
            }
            if ($embedded !== null) {
                return self::isPublicIp((string) inet_ntop($embedded));
            }
        }
        foreach (self::BLOCKED_NETS as $cidr) {
            if (self::inNet($packed, $cidr)) {
                return false;
            }
        }
        return true;
    }

    private static function inNet(string $packed, string $cidr): bool
    {
        [$net, $bits] = explode('/', $cidr);
        $netPacked = inet_pton($net);
        if ($netPacked === false || strlen($netPacked) !== strlen($packed)) {
            return false;
        }
        $bits = (int) $bits;
        $bytes = intdiv($bits, 8);
        if ($bytes > 0 && strncmp($packed, $netPacked, $bytes) !== 0) {
            return false;
        }
        $rest = $bits % 8;
        if ($rest === 0) {
            return true;
        }
        $mask = (0xff << (8 - $rest)) & 0xff;
        return (ord($packed[$bytes]) & $mask) === (ord($netPacked[$bytes]) & $mask);
    }

    /** Every address of the host when all are public; null otherwise (or when it does not resolve). */
    public static function publicAddresses(string $host): ?array
    {
        $host = trim($host, '[]');
        if ($host === '') {
            return null;
        }
        if (filter_var($host, FILTER_VALIDATE_IP)) {
            $addresses = [$host];
        } elseif (self::$resolver !== null) {
            $addresses = (array) (self::$resolver)($host);
        } else {
            $addresses = Cache::remember('image_proxy_dns:' . sha1(strtolower($host)), self::DNS_TTL_S,
                fn () => self::systemResolve($host));
        }
        $addresses = array_values(array_unique(array_filter(array_map('strval', $addresses))));
        if (!$addresses) {
            return null;
        }
        foreach ($addresses as $ip) {
            if (!self::isPublicIp($ip)) {
                return null;
            }
        }
        return $addresses;
    }

    private static function systemResolve(string $host): array
    {
        $v4 = @gethostbynamel($host) ?: [];
        $v6 = [];
        foreach ((@dns_get_record($host, DNS_AAAA) ?: []) as $record) {
            if (!empty($record['ipv6'])) {
                $v6[] = $record['ipv6'];
            }
        }
        return array_merge($v4, $v6);
    }

    // ------------------------------------------------------------------
    // Fetch
    // ------------------------------------------------------------------

    /**
     * ['status' => 200, 'body' => bytes] or ['status' => <http status>, 'error' => code] (bad_url | blocked |
     * upstream | too_large | redirects | unreachable). Never throws.
     */
    public static function fetch(string $url): array
    {
        $current = trim($url);
        try {
            for ($hop = 0; $hop <= self::MAX_REDIRECTS; $hop++) {
                $parts = parse_url($current);
                $parts = is_array($parts) ? $parts : [];
                $scheme = strtolower((string) ($parts['scheme'] ?? ''));
                $host = strtolower((string) ($parts['host'] ?? ''));
                if (!in_array($scheme, ['http', 'https'], true) || $host === ''
                    || isset($parts['user']) || preg_match('/[\x00-\x20\x7f\\\\]/', $current)) {
                    return ['status' => 400, 'error' => 'bad_url'];
                }
                $addresses = self::publicAddresses($host);
                if ($addresses === null) {
                    return ['status' => 403, 'error' => 'blocked'];
                }
                $response = self::request($current, $host, (int) ($parts['port'] ?? ($scheme === 'https' ? 443 : 80)),
                                          $addresses);
                $status = $response->status();
                $location = $response->header('Location');
                if ($status >= 300 && $status < 400 && $location !== '') {
                    self::close($response);
                    $current = (string) UriResolver::resolve(new Uri($current), new Uri($location));
                    continue;
                }
                if (!$response->successful()) {
                    self::close($response);
                    return ['status' => $status >= 400 && $status < 600 ? $status : 502, 'error' => 'upstream'];
                }
                if ((int) $response->header('Content-Length') > self::MAX_BYTES) {
                    self::close($response);
                    return ['status' => 413, 'error' => 'too_large'];
                }
                $body = self::readCapped($response);
                return $body === null ? ['status' => 413, 'error' => 'too_large'] : ['status' => 200, 'body' => $body];
            }
            return ['status' => 502, 'error' => 'redirects'];
        } catch (\Throwable $e) {
            return ['status' => 502, 'error' => 'unreachable'];
        }
    }

    private static function request(string $url, string $host, int $port, array $addresses): Response
    {
        $options = ['allow_redirects' => false, 'stream' => true];
        $bare = trim($host, '[]');
        if (!filter_var($bare, FILTER_VALIDATE_IP) && defined('CURLOPT_RESOLVE')) {
            // curl connects to the addresses just checked: no second DNS answer (rebinding) can point it inside
            $pinned = implode(',', array_map(fn ($ip) => str_contains($ip, ':') ? "[{$ip}]" : $ip, $addresses));
            $options['curl'] = [CURLOPT_RESOLVE => ["{$bare}:{$port}:{$pinned}"]];
        }
        return Http::withHeaders([
            'User-Agent' => 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept' => 'image/webp,image/png,image/jpeg,image/*;q=0.8',
        ])->timeout(self::TIMEOUT_S)->withOptions($options)->get($url);
    }

    private static function readCapped(Response $response): ?string
    {
        $stream = $response->toPsrResponse()->getBody();
        $body = '';
        while (!$stream->eof()) {
            $chunk = $stream->read(65536);
            if ($chunk === '') {
                break;
            }
            $body .= $chunk;
            if (strlen($body) > self::MAX_BYTES) {
                $stream->close();
                return null;
            }
        }
        $stream->close();
        return $body;
    }

    private static function close(Response $response): void
    {
        try {
            $response->toPsrResponse()->getBody()->close();
        } catch (\Throwable $e) {
            // best effort
        }
    }

    /** private, a day; immutable for a content-addressed URL (a Cloudinary version or a sha1 / sha256 in its path). */
    public static function cacheControl(string $url): string
    {
        $path = (string) parse_url($url, PHP_URL_PATH);
        $immutable = preg_match('~/v\d{6,}/~', $path) || preg_match('~(?:^|[/_.-])[0-9a-f]{40}(?:[0-9a-f]{24})?(?:[/_.-]|$)~i', $path);
        return 'private, max-age=86400' . ($immutable ? ', immutable' : '');
    }
}
