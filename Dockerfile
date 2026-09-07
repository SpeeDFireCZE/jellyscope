# ---------------------------------------------------------------------------
# Jellyscope v kontejneru.
#
#     docker compose up -d
#     http://localhost:8097
#
# Co je potřeba vědět předem:
#
#   * Data (databáze, log, mezipaměť obrázků, zálohy) bydlí ve svazku
#     připojeném do /app/data. Bez něj by po `docker compose down` zmizela
#     celá historie - tohle je jediná věc, o kterou v kontejneru jde přijít.
#   * Když sbíráš technické údaje přes ffprobe, musí kontejner na soubory
#     vidět: knihovnu připoj jen pro čtení a v Nastavení nastav mapování
#     cest z pohledu Jellyfinu na cestu v kontejneru.
#   * Zálohy si ukládej do /app/data (třeba /app/data/backups). Cokoliv
#     mimo připojené složky se uloží dovnitř kontejneru a při dalším
#     buildu je to pryč.
#   * Do Jellyfinu se jen čte. Kontejner nepotřebuje žádné právo navíc.
# ---------------------------------------------------------------------------

FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# ffprobe (součást ffmpeg) čte technické údaje přímo ze souborů - bez něj
# umí aplikace jen to, co hlásí Jellyfin. Je to volba při buildu:
#
#     docker compose build --build-arg FFPROBE=0
#
# ušetří ~250 MB obrazu a nechá zdroj dat na Jellyfinu.
ARG FFPROBE=1

# pg_dump (postgresql-client) je potřeba jen při zálohování PostgreSQL.
# Bez něj se aplikace zazálohuje vlastním exportem - funguje to, ale
# pg_dump umí konzistentní snímek, pořadí závislostí i indexy, takže
# ~30 MB v obrazu za to stojí. U SQLite se nepoužije vůbec.
ARG PGDUMP=1

RUN if [ "$FFPROBE" = "1" ] || [ "$PGDUMP" = "1" ]; then \
        apt-get update \
        && if [ "$FFPROBE" = "1" ]; then \
               apt-get install --no-install-recommends -y ffmpeg; \
           fi \
        && if [ "$PGDUMP" = "1" ]; then \
               apt-get install --no-install-recommends -y postgresql-client; \
           fi \
        && rm -rf /var/lib/apt/lists/*; \
    fi

WORKDIR /app

# Závislosti zvlášť a jako první vrstva: dokud se requirements.txt
# nezmění, další build je nepřeinstalovává znovu.
COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY . .

# Aplikace běží pod vlastním uživatelem s pevným UID - nikdy jako root.
# Pevné číslo proto, aby šlo připojené složce nastavit vlastníka i na
# hostiteli.
RUN useradd --create-home --uid 10001 jellyscope \
    && mkdir -p /app/data \
    && chown -R jellyscope:jellyscope /app

# setpriv zahazuje rootovská práva ve spouštěči (viz níž). V debianím
# základu bývá, ale spoléhat se na to nebudeme: chybějící nástroj by se
# projevil až na cizím serveru tím, že by aplikace běžela jako root.
RUN command -v setpriv >/dev/null 2>&1 \
    || (apt-get update \
        && apt-get install --no-install-recommends -y util-linux \
        && rm -rf /var/lib/apt/lists/*)

# Na 127.0.0.1 uvnitř kontejneru se zvenku nikdo nedovolá.
# JELLYSCOPE_DOCKER rekne aplikaci, ze bezi v kontejneru. Podle toho
# si sama nastavi slozku na zalohy do /app/data/backups - tedy do
# pripojene slozky, ke ktere se da dostat i z hostitele.
ENV HOST=0.0.0.0 \
    PORT=8097 \
    DATABASE_PATH=data/jellyscope.db \
    JELLYSCOPE_DOCKER=1

EXPOSE 8097

# Kontejner, který běží, ale neodpovídá, vypadá zvenku stejně jako zdravý.
# /setup odpoví i bez přihlášení, takže se hodí líp než domovská stránka.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8097/setup', timeout=4)"

# Kontejner startuje jako root JEN kvůli jedné věci: srovnat vlastníka
# připojené složky s daty. Ta na hostiteli patří tomu, kdo ji vyrobil -
# při prvním `docker compose up` tedy rootovi - a aplikace by se do ní
# nedostala. Spouštěč to spraví a hned nato práva zahodí; samotná
# aplikace běží jako jellyscope (UID 10001).
#
# Přes `sh` schválně: repozitář se vyvíjí i na Windows, kde se právo
# ke spuštění v gitu neudrží, a chybějící "x" by kontejner shodilo.
ENTRYPOINT ["/bin/sh", "/app/deploy/docker-entrypoint.sh"]
CMD ["python", "run.py"]
