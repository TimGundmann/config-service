# Knittynetwork serverovervågning

Kontroller hver time på `gundmann.dk`, med mail til `tim@gundmann.dk` ved nye eller forværrede problemer og ved genoprettelse. Uændrede hændelser giver ikke gentagne mails.

Kontroller: HTTPS med to forsøg, nginx-status, permanente filsystemer (disk og inoder >= 90 %), forventede Docker-containere, dynamisk aktiv blue/green-release, Docker-health, OOM, genstarter og nye ERROR/FATAL/PANIC/Exception-markeringer i containerlogs. Bevidst stoppede tidligere releases overvåges ikke. Logs indeholder kun antal og tidspunkter i mails; rå loglinjer, persondata og hemmeligheder sendes ikke.

Installeret placering: `/home/tim/.local/share/knitty-monitor`. Der kræves Python 3, Docker-adgang, cron og timeout. SMTP-indstillinger læses i hukommelsen fra den eksisterende `supabase-auth`-container og kopieres ikke til filer. TLS og certifikatkontrol er obligatorisk. SSH-nøglen på computeren bruges kun ved opsætning og vedligeholdelse; cron på serveren bruger ikke SSH.

```sh
python3 ~/.local/share/knitty-monitor/monitor.py --dry-run
python3 ~/.local/share/knitty-monitor/monitor.py --test-mail
cat ~/.local/share/knitty-monitor/status.json
crontab -l
```

`state.json` indeholder kontroltidspunkter og rapporterede hændelser. Ved mailfejl rykkes logtidspunkter ikke frem, så alarmen forsøges igen næste gang. `status.json` viser seneste kontrol og mailstatus. Kun én kørsel ad gangen tillades. Cron sætter en samlet tidsgrænse på 15 minutter.

Ved alarm: kontroller den nævnte service med `docker inspect` og `docker logs --since 1h NAVN`. Ved diskalarm: `df -h` og `df -i`; vælg selv en eventuel oprydning. Overvågningen ændrer eller genstarter ikke services.

Begrænsninger: containerlogs dækker stdout/stderr, ikke private logfiler inde i containeren. `tim` har ikke generel adgang til root/systemlogs. Serveren kan ikke selv sende en mail ved totalt servernedbrud, tab af netværk, stoppet cron eller fejl i mailudbyderen. Det kræver en separat ekstern watchdog. SMTP-accept er ikke bevis for levering til indbakken. Aktiver mailnotifikationer på iPhone for den valgte mailkonto.

Fjern linjen mærket `# knitty-monitor` fra `crontab -e` for at stoppe den timevise kørsel. Bevar andre cronjobs.

## Docker-logrotation

Aktiveret 31. oktober 2026: applikationer, Nginx-proxyer og Supabase bruger
`json-file` med `max-size=10m` og `max-file=3`. Docker roterer selv logfilerne;
overvågningens cronjob foretager fortsat ingen oprydning.

Alle fem service-repositories angiver grænserne ved oprettelse af containere.
Supabase har dem i `/home/tim/git/supabase-project/docker-compose.local.yml`,
som indgår i projektets eksisterende Compose-filrækkefølge. Den oprindelige fil
er bevaret med suffikset `.before-log-rotation`.

Proxyernes oprindelige containere er bevaret stoppet med suffikset
`-before-log-rotation`; de indeholder historiske logs og vokser ikke videre.
Blue/green-deployet bevarer desuden sin tidligere applikationsrelease til rollback.
Rotation gælder de nye aktive containere, ikke disse stoppede arkiver.

Migration og kontrol ligger i `/home/tim/knitty-log-rotation-20261031`.
Kør den læsende slutkontrol med:

```sh
python3 /home/tim/knitty-log-rotation-20261031/verify_log_rotation.py
```

Global Docker-standard er ikke ændret. Nye containere uden for disse deploy-
og Compose-opsætninger skal også have loggrænser angivet ved oprettelse.

## Portadgang og alarmtekster, 1. november 2026

Poolerportene 5432/6543 og analytics-port 4000 er nu kun bundet til 127.0.0.1.
Intern Docker-adgang er bevaret. Fjernadministration kræver SSH-tunnel.
Den eksisterende docker-compose.local.yml bruger !override til at erstatte offentlige bindinger.
Mails med udelukkende logfejl hedder Logadvarsel; logfejl bliver fortsat registreret.
Ophørte logmarkeringer beskrives som Ingen nye fejlmarkeringer fremfor som en repareret tjeneste.

Serverbackup før ændringen: /home/tim/.local/share/knitty-monitor/port-hardening-20261101T111340Z.
Migrationen ligger i restrict_supabase_ports.py og er beregnet til engangskørsel på serveren.
Lokale overvågningskilder er hentet fra serveren, inklusive eksisterende incident-integration.
Verifikation: 15 tests bestået; begge containere healthy; overvågningens dry-run fandt ingen problemer.
