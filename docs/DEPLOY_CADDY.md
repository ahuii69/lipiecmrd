# AI-Hub — Deploy Caddy HTTPS

## Wymagania

- Caddy 2.x zainstalowany (`caddy version`)
- Domena (np. `ahui69.org`) skierowana na IP serwera (A record)
- Port 80 i 443 otwarty (Caddy potrzebuje na ACME challenge)
- Backend AI-Hub uruchomiony na `127.0.0.1:PORT`

## Instalacja Caddy

### Automatycznie (przez start.sh)

```bash
./start.sh --install-caddy --caddy
```

### Ręcznie (Debian/Ubuntu)

```bash
apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | tee /etc/apt/sources.list.d/caddy-stable.list
apt update && apt install -y caddy
```

## Caddyfile

Generowany automatycznie przez `./start.sh --caddy`. Lokalizacja: `/etc/caddy/Caddyfile`

```caddyfile
ahui69.org {
    encode zstd gzip

    reverse_proxy 127.0.0.1:8080

    header {
        X-Content-Type-Options nosniff
        X-Frame-Options DENY
        Referrer-Policy strict-origin-when-cross-origin
        Strict-Transport-Security "max-age=63072000; includeSubDomains; preload"
        -Server
    }
}
```

Port `8080` jest zastępowany aktualnym portem z `data/run/aihub.port`.

## Komendy

```bash
# Walidacja konfiguracji
caddy validate --config /etc/caddy/Caddyfile

# Status
systemctl status caddy

# Reload (po zmianach Caddyfile)
systemctl reload caddy

# Restart
systemctl restart caddy

# Logi
journalctl -u caddy -f
```

## Test

```bash
# Czy backend żyje (lokalnie)?
curl -s http://127.0.0.1:$(cat data/run/aihub.port)/system/ping

# Czy HTTPS działa?
curl -s https://ahui69.org/system/ping
curl -s https://ahui69.org/cognitive/health | python3 -m json.tool
```

## Troubleshooting

| Objaw              | Sprawdź                                                                          |
| ------------------ | -------------------------------------------------------------------------------- |
| 502 Bad Gateway    | Backend nie działa → `cat data/run/aihub.pid`, `curl localhost:PORT/system/ping` |
| Cert error         | DNS nie wskazuje na serwer, port 80/443 zablokowany                              |
| validate fail      | Składnia Caddyfile → `caddy validate --config /etc/caddy/Caddyfile`              |
| Caddy nie startuje | `journalctl -u caddy -n 50`                                                      |

## Backup

start.sh automatycznie tworzy backup poprzedniego Caddyfile:

```
/etc/caddy/Caddyfile.bak.<timestamp>
```
