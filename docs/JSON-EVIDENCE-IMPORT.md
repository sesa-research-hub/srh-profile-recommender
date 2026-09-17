# Importare una campagna di evidenze SRH

L'import JSON serve a valutare campagne eseguite su altri sistemi. Per i modelli raggiungibili dalla macchina corrente, usare il pannello **Confronto live guidato**: rileva i runtime, applica lo stesso protocollo e raccoglie le misure senza comporre file a mano.

## File richiesti

1. Un Workload Contract con `schema: srh.workload-contract.v1`. Definisce input, output, concorrenza, SLO e qualità attesa.
2. Un Candidate Manifest con `schema: srh.deployment-candidates.v1`, `policy` uguale a `performance_first` o `cost_first` e almeno due candidati.
3. Almeno due evidenze grezze con `schema: srh.paired-response-experiment.v1`, una per ogni profilo osservato.

Un candidato del manifest ha questa forma minima:

```json
{
  "profile_id": "srh-0123456789ab",
  "label": "Nome leggibile del deployment",
  "license_review_status": "review_required",
  "estimated_three_year_cost_eur": null,
  "cost_provenance": null,
  "device_power_w": 240
}
```

Usare `license_review_status: approved` solo dopo una revisione umana. Se è presente un costo, `cost_provenance` deve essere `supplier_quote`, `calculated` o `customer_provided`. La politica `cost_first` richiede un costo per ogni candidato idoneo.

## Come deve essere prodotta l'evidenza

Ogni evidenza deve contenere:

- il profilo runtime catturato dal processo realmente in esecuzione;
- lo stesso `experiment`, `scenario`, `comparison` e `protocol` degli altri candidati;
- almeno cinque ripetizioni complete;
- i batch grezzi di probe e risposta, inclusi errori e tempi;
- la verifica che l'identità runtime sia rimasta stabile fino alla fine della misura;
- ground truth ed evaluator versionati.

Il recommender ricalcola KPI e qualità dai batch grezzi. Le sole summary salvate nel file non possono cambiare l'esito. Alias diversi che puntano allo stesso profilo runtime non sono candidati distinti.

## Controllo prima dell'import

- Il `profile_id` di ciascuna evidenza compare una sola volta nel manifest.
- Gli hash di scenario pack, ground truth ed evaluator coincidono fra i candidati.
- Concorrenza, token, cache, reasoning e ordine del protocollo coincidono.
- Non sono state rimosse richieste fallite.
- I file descrivono lo stesso Workload Contract selezionato nell'interfaccia.

Un pacchetto completo di esempio è disponibile dall'interfaccia. È materiale dimostrativo e non costituisce evidenza per il cliente corrente.
