# Telco Benchmark

Sito pubblico che confronta i mercati telecomunicazioni nazionali su contesto, domanda, struttura competitiva, economics, investimenti e rete. Il pilota copre i Big 5 europei (Italia, Germania, Francia, Spagna, Regno Unito).

Il sito è un file HTML statico, rigenerato e pubblicato su GitHub Pages da GitHub Actions. Non dipende da nessun account personale né da servizi a pagamento.

## Come funziona l'aggiornamento

| Tipo di dato | Fonte | Come si aggiorna |
|---|---|---|
| Popolazione, densità, PIL, abbonamenti per 100 ab., utenti internet | World Bank API (include dati ITU) | Automatico, il 2 di ogni mese |
| Cambi GBP/USD → EUR | BCE Data API | Automatico, il 2 di ogni mese |
| Spagna: linee mobili, fisse, FTTH, ricavi mobili, traffico dati (trimestrali) | CNMC, API CKAN | Automatico, con controllo di plausibilità |
| Francia: SIM, abbonamenti FTTH, investimenti | Arcep, open data su data.gouv.fr | Automatico, con controllo di plausibilità |
| Copertura 5G e FTTP (UE) | Commissione europea, Digital Decade | File CSV, una volta l'anno (giugno) |
| Ricavi, SIM, traffico, capex, quote | AGCOM, BNetzA, Arcep, CNMC, Ofcom | File CSV, dopo i report annuali o trimestrali |
| Velocità mobile | Ookla Speedtest Global Index | File CSV |
| ARPU, HHI, capex su ricavi, ricavi su PIL, per abitante | Calcolati | Automatico a ogni build |

Se una fonte automatica non risponde, resta l'ultimo dato valido. Un valore dei regolatori ottenuto via API sostituisce quello curato solo se è più recente, rientra nell'intervallo plausibile (`range` in `config/kpis.json`) e non se ne discosta oltre il 35%. Altrimenti resta il curato e il valore scartato finisce nella issue automatica. I connettori CNMC e Arcep derivano da quelli del progetto global-telco-intelligence e scartano qualsiasi totale che non sia ricostruibile in modo univoco. Quando una fonte curata supera la scadenza impostata in `config/sources.json`, il workflow apre una issue con l'etichetta `dati-da-aggiornare` e l'elenco di cosa rinnovare.

I report dei regolatori non hanno API: un'automazione che li legge dai PDF si romperebbe a ogni cambio di impaginazione. Per questo la parte curata è un CSV versionato, dove ogni riga ha fonte, periodo, data di raccolta e nota.

## Messa online (una volta sola, circa 5 minuti, tutto dal browser)

1. Crea un repository **pubblico** su GitHub con l'opzione "Add a README" attiva.
2. In **Settings → Pages**, alla voce Source, scegli **GitHub Actions**.
3. **Add file → Upload files**: carica lo zip del progetto **così com'è, senza estrarlo**, e fai *Commit changes*.
4. **Add file → Create new file**: come nome scrivi `.github/workflows/update.yml`, incolla il contenuto del file omonimo presente nello zip e fai *Commit changes*.

Il workflow parte da solo: estrae lo zip nelle cartelle giuste, cancella lo zip, scarica i dati, genera il sito e lo pubblica. L'indirizzo compare in *Settings → Pages* (di solito `https://<account>.github.io/<repository>/`).

**Aggiornare il codice in futuro**: carica il nuovo zip con *Upload files*. Il workflow lo estrae e ripubblica. Unica eccezione: il file del workflow va modificato a mano, perché GitHub non permette alle automazioni di cambiare se stesse.

## Aggiornare un dato curato

Apri `data/curated/kpi_values.csv` (anche dall'editor web di GitHub), modifica `value`, `period`, `year`, `source_url` e la data in `retrieved`, poi salva con un commit. Il sito viene ricostruito in un paio di minuti.

Le colonne sono:

- `currency`: lasciala vuota per valori non monetari. Per i valori in GBP scrivi `GBP` e la conversione in euro avviene con il cambio medio BCE dell'anno.
- `confidence`: scegli tra `ufficiale`, `calcolato`, `secondaria` e `stima`.
- `note`: scrivi qui la definizione usata dalla fonte, che è fondamentale per il confronto.

Le quote di mercato stanno in `data/curated/market_shares.csv`. Da qui si calcolano HHI e quota del leader.

## Aggiungere un paese

1. Aggiungi l'oggetto in `config/countries.json`, con `iso3` per World Bank, superficie, valuta e operatori di rete.
2. Aggiungi il colore `--c-XX` nel CSS di `site/template.html` (tema chiaro e scuro).
3. Aggiungi le righe del paese nei due CSV curati.

I dati World Bank arrivano da soli al primo run.

## Aggiungere un indicatore

- **Indicatore World Bank**: basta una riga in `config/kpis.json` con il codice `wb`.
- **Indicatore curato**: una riga nel catalogo più i valori nel CSV.
- **Indicatore derivato**: una riga nel catalogo più il calcolo in `etl/build.py`, nel metodo `compute_derived`.

## Sviluppo locale

```bash
pip install -r requirements.txt
cd etl
python update_all.py            # facoltativo, richiede internet: World Bank, BCE, CNMC, Arcep
python build.py                 # genera dist/index.html, dist/data.json, dist/data.csv
cd ..
python -m pytest etl/tests
```

Apri `dist/index.html` nel browser: non serve un server.

## Cautele di confronto

Ogni indicatore ha una lettera di confrontabilità:

- **A**: metodologia armonizzata.
- **B**: definizioni nazionali simili.
- **C**: definizioni diverse.

I casi principali sono questi:

- **Ricavi del settore**: Italia, Germania e Regno Unito includono il wholesale. Francia e Spagna sono solo retail, e la Spagna esclude anche audiovisivo e terminali. Il capex su ricavi della Spagna risulta quindi sovrastimato rispetto agli altri.
- **Quote di mercato**: per Italia e Spagna le quote vengono dal regolatore (SIM human, linee mobili). Per la Germania sono ricostruite dai contratti postpaid dichiarati dai quattro operatori. Per la Francia sono una stima da fonti secondarie, perché Arcep non pubblica quote per operatore. Il Regno Unito non ha ancora una fonte pubblica verificabile.
- **Copertura nel Regno Unito**: Ofcom misura 5G e fibra su unità immobiliari e con una metodologia diversa dalla Commissione UE.
- **Traffico dati per SIM**: la Germania divide per tutti i profili SIM attivi, la Francia per i soli clienti 4G attivi.
- **ARPU calcolato**: per Francia e Regno Unito coincide con quello pubblicato dal regolatore (14,30 € e 13,24 £), il che conferma il metodo.

## Dati mancanti

- **Quote di mercato mobile del Regno Unito (e quindi HHI)**: VodafoneThree e Virgin Media O2 pubblicano la propria base clienti, BT/EE no. Ofcom non pubblica quote per operatore. Il dato si ottiene solo da fonti a pagamento (per esempio GSMA Intelligence) o da un'eventuale ripresa della disclosure di BT.
- **Velocità fissa mediana**: fonte Ookla, da aggiungere.
- **Capex del Regno Unito**: è del 2024 (stima Ofcom, prezzi 2024), non del 2025. Il capex su ricavi confronta quindi capex 2024 con ricavi 2025.
- **Indicatori World Bank**: popolazione urbana, PIL PPP, abbonamenti ogni 100 abitanti e utenti internet si popolano al primo run del workflow.

## Licenze dei dati

- World Bank, BCE e Commissione europea: riuso libero con attribuzione.
- Velocità Ookla: riprese dal dataset DriftEuropa (CC BY 4.0), che le cita da Speedtest Global Index.
- Dati dei regolatori nazionali: riuso con citazione della fonte, riportata su ogni valore.
