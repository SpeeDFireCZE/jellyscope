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
RUN if [ "$FFPROBE" = "1" ]; then \
        apt-get update \
        && apt-get install --no-install-recommends -y ffmpeg \
        && rm -rf /var/lib/apt/lists/*; \
    fi

WORKDIR /app

# Závislosti zvlášť a jako první vrstva: dokud se requirements.txt
# nezmění, další build je nepřeinstalovává znovu.
COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY . .

# Nic tu neběží jako root. Uživatel má pevné UID, aby šlo svazku s daty
# nastavit vlastníka na hostiteli (chown -R 10001:10001 ./data).
RUN useradd --create-home --uid 10001 jellyscope \
    && mkdir -p /app/data \
    && chown -R jellyscope:jellyscope /app
USER jellyscope

# Na 127.0.0.1 uvnitř kontejneru se zvenku nikdo nedovolá.
ENV HOST=0.0.0.0 \
    PORT=8097 \
    DATABASE_PATH=data/jellyscope.db

EXPOSE 8097

# Kontejner, který běží, ale neodpovídá, vypadá zvenku stejně jako zdravý.
# /setup odpoví i bez přihlášení, takže se hodí líp než domovská stránka.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8097/setup', timeout=4)"

CMD ["python", "run.py"]
