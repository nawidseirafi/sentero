# Sentero v14 – Update-Zuverlaessigkeit

## Ursache

`docker compose up -d sentero` startet standardmaessig auch `depends_on`-Dienste. Auf einer frisch provisionierten Box waren Mosquitto/Ollama noch nicht gestartet. Dadurch zog Docker Compose beim App-Update unter anderem `ollama/ollama:latest`; der eigentliche Sentero-Neustart verzögerte sich um mehrere Minuten. Parallel markierte das Backend den synchronen Updater-Socket-Timeout nach 10 Sekunden faelschlich als Updatefehler.

## Aenderungen

- Host-Updater aktualisiert nur den Sentero-Container mit `--no-deps --force-recreate`.
- Compose-Recreate hat ein kontrolliertes 90-Sekunden-Limit.
- Host-Updater schreibt START/DONE/FAILED/TIMEOUT inklusive Laufzeit ins systemd-Journal.
- Ein Socket-Timeout/Connection-Reset nach gestarteter Installationsanfrage bleibt im Backend `running` statt `failed`.
- Frontend pollt bis zu 15 Minuten weiter und wertet nur einen echten finalen Host-/App-Status als Erfolg oder Fehler.

Damit werden langsame oder unterbrochene HTTP-Verbindungen waehrend des Container-Recreates nicht mehr als falscher Updatefehler angezeigt.
