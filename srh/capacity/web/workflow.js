const reportNumber = value => value == null || !Number.isFinite(Number(value)) ? 'n/d' : Number(value).toLocaleString('it-IT', {maximumFractionDigits: 2});
const reportSeconds = value => value == null || !Number.isFinite(Number(value)) ? 'n/d' : `${reportNumber(Number(value) / 1000)} s`;
const reportRangeSeconds = metric => `${reportNumber(metric.low / 1000)}–${reportNumber(metric.high / 1000)} s`;
const reportDate = value => new Intl.DateTimeFormat('it-IT', {dateStyle: 'long'}).format(value);

function setWorkflowStep(step, state) {
  const item = document.querySelector(`[data-workflow-step="${step}"]`);
  if (!item) return;
  item.dataset.state = state;
  item.setAttribute('aria-current', state === 'active' ? 'step' : 'false');
}

function currentRecommendationIsForAssessment() {
  return Boolean(
    latestPlan && latestRecommendation &&
    latestRecommendation.contract_sha256 === latestPlan.integrity.translated_contract_sha256
  );
}

function workflowSnapshot() {
  const currentRecommendation = currentRecommendationIsForAssessment();
  const observedRuns = localBenchmarkRuns.length;
  const referenceRuns = referenceSimulationRuns.length;
  return {
    schema: 'srh.assessment-session.v1',
    exported_at: new Date().toISOString(),
    assessment_id: latestPlan?.assessment_id || null,
    capacity_plan: latestPlan,
    readiness_observations: localBenchmarkRuns,
    reference_simulations: referenceSimulationRuns,
    deployment_recommendation: currentRecommendation ? latestRecommendation : null,
    excluded_demo_recommendation: latestRecommendation && !currentRecommendation ? {
      schema: latestRecommendation.schema,
      workload_id: latestRecommendation.workload_id,
      verdict: latestRecommendation.verdict,
      reason: 'The measured demonstration uses a different workload contract and is excluded from the client decision.',
    } : null,
    evidence_summary: {
      observed_readiness_runs: observedRuns,
      reference_simulations: referenceRuns,
      verified_deployment_recommendation: currentRecommendation,
    },
  };
}

function updateWorkflow() {
  const hasPlan = Boolean(latestPlan);
  const hasReadiness = localBenchmarkRuns.length > 0;
  const hasAnyTest = hasReadiness || referenceSimulationRuns.length > 0;
  const hasRecommendation = currentRecommendationIsForAssessment();

  setWorkflowStep('discovery', hasPlan ? 'complete' : 'active');
  setWorkflowStep('capacity', !hasPlan ? 'locked' : hasAnyTest ? 'complete' : 'active');
  setWorkflowStep('benchmark', !hasPlan ? 'locked' : hasAnyTest ? 'complete' : 'active');
  setWorkflowStep('recommendation', !hasPlan ? 'locked' : hasRecommendation ? 'complete' : hasReadiness ? 'active' : 'ready');
  setWorkflowStep('report', !hasPlan ? 'locked' : 'ready');

  if (hasPlan) {
    $('#benchmark-workflow').classList.remove('hidden');
    $('#recommendation-workflow').classList.remove('hidden');
    $('#report-workflow').classList.remove('hidden');
  }

  const evidenceStatus = $('#workflow-evidence-status');
  if (evidenceStatus && hasPlan) {
    const recommendationText = hasRecommendation
      ? 'Confronto misurato collegato al contratto cliente disponibile.'
      : 'Recommendation verificata non ancora disponibile.';
    evidenceStatus.innerHTML = `<strong>${hasReadiness ? `${localBenchmarkRuns.length} test locali osservati` : 'Nessun test locale osservato'}</strong><span>${referenceSimulationRuns.length} simulazioni di riferimento · ${recommendationText}</span>`;
  }
  updateReportReadiness();
}

function workflowPlanUpdated() {
  latestRecommendation = null;
  referenceSimulationRuns = [];
  updateWorkflow();
}

function workflowEvidenceUpdated() {
  updateWorkflow();
}

function workflowRecommendationUpdated() {
  updateWorkflow();
}

function workflowStatus() {
  if (currentRecommendationIsForAssessment()) {
    if (latestRecommendation.verdict === 'RECOMMENDED') {
      return {code: 'VERIFIED_RECOMMENDATION', label: 'Recommendation verificata', tone: 'verified'};
    }
    if (latestRecommendation.verdict === 'NO_DEPLOYMENT_MEETS_REQUIREMENTS') {
      return {code: 'VERIFIED_NO_MATCH', label: 'Misure verificate: nessun candidato idoneo', tone: 'attention'};
    }
    return {code: 'MEASURED_INCONCLUSIVE', label: 'Confronto misurato non conclusivo', tone: 'attention'};
  }
  if (localBenchmarkRuns.length) {
    return {code: 'READINESS_OBSERVED', label: 'Readiness osservata — decisione preliminare', tone: 'observed'};
  }
  return {code: 'PRELIMINARY_SCREENING', label: 'Screening preliminare — misure richieste', tone: 'preliminary'};
}

function updateReportReadiness() {
  const box = $('#report-readiness');
  if (!box || !latestPlan) return;
  const status = workflowStatus();
  box.className = `report-readiness ${status.tone}`;
  box.innerHTML = `<strong>${esc(status.label)}</strong><span>${currentRecommendationIsForAssessment() ? 'Il dossier includerà la decisione basata sulle evidenze comparabili.' : 'Il dossier può essere emesso come assessment preliminare; indicherà esplicitamente le misure ancora necessarie.'}</span>`;
}

function reportExecutiveText(status) {
  if (status.code === 'VERIFIED_RECOMMENDATION') {
    return `Le evidenze comparabili indicano ${latestRecommendation.recommended_label} come configurazione preferibile per il perimetro misurato. Qualità, obiettivi di servizio e revisione della licenza risultano compatibili con i criteri inseriti. Prima della produzione resta necessario un test di accettazione sul sito cliente.`;
  }
  if (status.code === 'VERIFIED_NO_MATCH') {
    return 'Le misure raccolte sono confrontabili, ma nessuna configurazione supera contemporaneamente tutti i requisiti. Il risultato evita una scelta prematura e indirizza una nuova iterazione su applicazione, modello, infrastruttura o obiettivi di servizio.';
  }
  if (status.code === 'MEASURED_INCONCLUSIVE') {
    return 'Sono disponibili evidenze misurate, ma il confronto non è ancora sufficiente o pienamente comparabile. Il dossier conserva i risultati e specifica le condizioni necessarie per arrivare a una decisione verificata.';
  }
  if (status.code === 'READINESS_OBSERVED') {
    return 'La capacità è stata analizzata e almeno un runtime locale è stato misurato con un test di readiness. Le osservazioni descrivono il comportamento del test sintetico; servono almeno due deployment comparabili sul caso cliente per emettere una recommendation verificata.';
  }
  return 'L’assessment traduce i requisiti aziendali in un contratto di workload e individua le configurazioni da portare al benchmark. Le prestazioni riportate sono proiezioni di pianificazione e non costituiscono ancora una scelta di deployment.';
}

function reportShortlistRows(plan) {
  const shortlist = new Set(plan.screening_summary.shortlist_candidate_ids);
  return plan.candidates.filter(value => shortlist.has(value.candidate_id)).map(value => {
    const metrics = value.performance_projection.metrics;
    return `<tr><td><strong>${esc(value.hardware.name)}</strong><small>${value.hardware.power_w} W · ${esc(value.hardware.class)}</small></td><td>${esc(value.model.name)}<small>${value.weight_bits} bit · ${esc(value.model.architecture)}</small></td><td>${value.capacity.estimated_total_required_gib} / ${value.capacity.usable_memory_gib} GiB</td><td>${reportRangeSeconds(metrics.ttfa_p95_ms)}</td><td>${reportRangeSeconds(metrics.end_to_end_p95_ms)}</td><td>${esc(labels[value.screening_status])}<small>${esc(compactRationaleLabels[value.screening_status])}</small></td></tr>`;
  }).join('');
}

function reportReadinessRows() {
  if (!localBenchmarkRuns.length) {
    return '<tr><td colspan="6">Nessun benchmark locale osservato nella sessione.</td></tr>';
  }
  return localBenchmarkRuns.map(value => {
    const summary = value.summary;
    return `<tr><td>${esc(value.model)}<small>${esc(value.runtime_root || 'runtime non identificato')}</small></td><td>${esc(value.profile)}</td><td>${reportNumber(summary.ttfa_seconds?.p95)} s</td><td>${reportNumber(summary.elapsed_seconds?.p95)} s</td><td>${reportNumber(summary.answer_tokens_per_second?.min)} tok/s</td><td>${value.objective_summary.met}/${value.objective_summary.total}</td></tr>`;
  }).join('');
}

function reportReferenceRows() {
  if (!referenceSimulationRuns.length) {
    return '<tr><td colspan="4">Nessuna simulazione nominativa eseguita nella sessione.</td></tr>';
  }
  return referenceSimulationRuns.flatMap(report => report.candidates.map(value =>
    `<tr><td>${esc(report.model.name)}<small>${esc(report.model.source_type || 'riferimento esterno')} · non installato</small></td><td>${esc(value.hardware.name)}</td><td>${value.capacity.estimated_total_required_gib} / ${value.capacity.usable_memory_gib} GiB</td><td>${esc(labels[value.screening_status])}<small>${esc(compactRationaleLabels[value.screening_status])}</small></td></tr>`
  )).join('');
}

function reportRecommendationSection() {
  if (!currentRecommendationIsForAssessment()) {
    return `<div class="report-decision pending"><strong>Recommendation di deployment non ancora emessa</strong><p>Occorrono almeno due configurazioni distinte, misurate con lo stesso scenario, ground truth, evaluator, esperimento e protocollo. Le licenze devono essere revisionate e i costi devono avere una provenienza dichiarata quando la politica è cost-first.</p></div>`;
  }
  const report = latestRecommendation;
  const title = report.verdict === 'RECOMMENDED'
    ? `Configurazione raccomandata: ${esc(report.recommended_label)}`
    : report.verdict === 'NO_DEPLOYMENT_MEETS_REQUIREMENTS'
      ? 'Nessuna configurazione supera tutti i requisiti'
      : 'Evidenze non ancora sufficienti';
  const rows = report.candidates.map(value => `<tr><td>${esc(value.deployment.label)}</td><td>${esc(value.deployment.model?.snapshot || value.deployment.model?.served_names?.[0] || 'n/d')}</td><td>${reportSeconds(value.checks.ttfa_p95_ms.actual)}</td><td>${reportSeconds(value.checks.end_to_end_p95_ms.actual)}</td><td>${Math.round(value.checks.quality_minimum_score.actual * 100)}%</td><td>${value.eligible ? 'Idoneo' : esc(value.blocking_objectives.join(', '))}</td></tr>`).join('');
  return `<div class="report-decision ${report.verdict === 'RECOMMENDED' ? 'approved' : 'pending'}"><strong>${title}</strong><p>${esc(report.policy_explanation)}</p></div><table class="report-table"><thead><tr><th>Deployment</th><th>Modello</th><th>TTFA p95</th><th>E2E p95</th><th>Qualità</th><th>Esito</th></tr></thead><tbody>${rows}</tbody></table>`;
}

function buildConsultingReport() {
  if (!latestPlan) throw new Error('Calcola prima la shortlist del cliente.');
  const plan = latestPlan;
  const intake = plan.client_inputs;
  const contract = plan.translated_workload_contract;
  const status = workflowStatus();
  const preparedBy = $('#report-prepared-by').value.trim() || 'Sesa Research Hub';
  const notes = $('#report-notes').value.trim();
  const delivery = $('#report-delivery').value;
  const now = new Date();
  const assumptions = [
    `Input impegnativo: ${contract.request_profile.input_tokens.p95.toLocaleString('it-IT')} token.`,
    `Risposta impegnativa: ${contract.request_profile.output_tokens.p95.toLocaleString('it-IT')} token.`,
    `Concorrenza richiesta: ${Math.max(...contract.traffic.concurrent_users)} utenti simultanei.`,
    `Le proiezioni non sostituiscono benchmark sul checkpoint, runtime e hardware esatti.`,
  ];
  const nextActions = currentRecommendationIsForAssessment() && latestRecommendation.verdict === 'RECOMMENDED'
    ? ['Confermare condizioni economiche e responsabilità del fornitore.', 'Eseguire il test di accettazione sul sito cliente.', 'Definire monitoraggio, sicurezza, backup e gestione del ciclo di vita.']
    : ['Selezionare almeno due deployment distinti dalla shortlist.', 'Eseguire lo stesso scenario con documenti e ground truth approvati dal cliente.', 'Revisionare licenze e costi, quindi rieseguire il Deployment Recommender.'];

  $('#consulting-report').innerHTML = `
    <header class="report-cover">
      <div><span>SESA RESEARCH HUB</span><strong>SRH Private AI Recommender</strong></div>
      <p>Assessment di capacità e decision evidence</p>
      <h1>${esc(intake.name)}</h1>
      <div class="report-status ${status.tone}">${esc(status.label)}</div>
      <dl><div><dt>Assessment</dt><dd>${esc(plan.assessment_id)}</dd></div><div><dt>Data</dt><dd>${reportDate(now)}</dd></div><div><dt>Preparato da</dt><dd>${esc(preparedBy)}</dd></div><div><dt>Destinazione</dt><dd>${esc(delivery)}</dd></div></dl>
    </header>
    <section class="report-section"><span class="report-kicker">01 · SINTESI ESECUTIVA</span><h2>Indicazione per la decisione</h2><p class="report-lead">${esc(reportExecutiveText(status))}</p>${notes ? `<div class="report-note"><strong>Nota del consulente</strong><p>${esc(notes)}</p></div>` : ''}</section>
    <section class="report-section"><span class="report-kicker">02 · PERIMETRO</span><h2>Requisiti raccolti con il cliente</h2><div class="report-facts"><div><strong>${intake.traffic.total_users}</strong><span>utenti totali</span></div><div><strong>${intake.traffic.concurrent_users}</strong><span>utenti simultanei</span></div><div><strong>${intake.traffic.requests_per_day}</strong><span>richieste/giorno</span></div><div><strong>${intake.experience.complete_response_seconds} s</strong><span>risposta completa</span></div></div><table class="report-table"><tbody><tr><th>Utilizzo</th><td>${esc(intake.workload_class)}</td><th>Residenza dati</th><td>${esc(intake.deployment.data_residency)}</td></tr><tr><th>Documenti</th><td>${intake.documents.pages.p50}–${intake.documents.pages.max} pagine</td><th>Modalità</th><td>${esc(intake.documents.context_mode)}</td></tr><tr><th>Qualità</th><td colspan="3">Grounding ${intake.quality.grounding_required ? 'richiesto' : 'facoltativo'} · Citazioni ${intake.quality.citation_required ? 'richieste' : 'facoltative'} · Soglia ${Math.round(intake.quality.minimum_score * 100)}%</td></tr></tbody></table></section>
    <section class="report-section report-page-break"><span class="report-kicker">03 · CAPACITY PLANNING</span><h2>Configurazioni prioritarie da verificare</h2><p>Questa tabella è uno screening: memoria e vincoli sono calcolati; le prestazioni sono proiezioni con provenienza e incertezza dichiarate.</p><table class="report-table shortlist-report"><thead><tr><th>Ambiente</th><th>Classe modello</th><th>Memoria</th><th>Avvio</th><th>Risposta</th><th>Priorità</th></tr></thead><tbody>${reportShortlistRows(plan)}</tbody></table><div class="report-note"><strong>Assunzioni principali</strong><ul>${assumptions.map(value => `<li>${esc(value)}</li>`).join('')}</ul></div></section>
    <section class="report-section"><span class="report-kicker">04 · EVIDENZE</span><h2>Test e simulazioni raccolti</h2><h3>Readiness osservata</h3><table class="report-table"><thead><tr><th>Runtime</th><th>Profilo</th><th>TTFA p95</th><th>E2E p95</th><th>Velocità min.</th><th>Obiettivi</th></tr></thead><tbody>${reportReadinessRows()}</tbody></table><h3>Simulazioni nominative non misurate</h3><table class="report-table"><thead><tr><th>Modello di riferimento</th><th>Hardware</th><th>Memoria</th><th>Screening</th></tr></thead><tbody>${reportReferenceRows()}</tbody></table><p class="report-caption">Test locali osservati: ${localBenchmarkRuns.length}. Simulazioni di riferimento non misurate: ${referenceSimulationRuns.length}. I due tipi di evidenza non vengono confusi nel verdetto.</p></section>
    <section class="report-section report-page-break"><span class="report-kicker">05 · RECOMMENDATION</span><h2>Decisione e condizioni</h2>${reportRecommendationSection()}</section>
    <section class="report-section"><span class="report-kicker">06 · PIANO D’AZIONE</span><h2>Passi successivi</h2><ol class="report-actions">${nextActions.map(value => `<li>${esc(value)}</li>`).join('')}</ol><div class="report-options"><div><strong>Affidamento a SRH</strong><p>SRH può usare contratto, manifest ed evidenze del dossier come base per progettazione, benchmark estesi e test di accettazione.</p></div><div><strong>Affidamento a un fornitore terzo</strong><p>Il dossier definisce requisiti, shortlist, condizioni di prova e limiti che il fornitore dovrà confermare sul proprio deployment.</p></div></div></section>
    <section class="report-section report-governance"><span class="report-kicker">07 · TRACCIABILITÀ</span><h2>Provenienza e limiti</h2><ul><li>Requisiti: dichiarazioni raccolte durante la discovery.</li><li>Capacità: calcoli SRH su cataloghi versionati.</li><li>Prestazioni stimate: benchmark vendor scalati o roofline SRH, sempre etichettati.</li><li>Readiness: osservazioni locali su scenario sintetico; non equivalgono a collaudo cliente.</li><li>Recommendation: emessa solo su evidenze comparabili e licenze revisionate.</li></ul><dl class="report-hashes"><div><dt>Intake SHA-256</dt><dd>${plan.integrity.client_intake_sha256}</dd></div><div><dt>Contract SHA-256</dt><dd>${plan.integrity.translated_contract_sha256}</dd></div><div><dt>Planner</dt><dd>${esc(plan.planner_version)}</dd></div><div><dt>Stato</dt><dd>${esc(status.code)}</dd></div></dl><p class="report-caption">Questo documento supporta una decisione tecnica e commerciale nel perimetro dichiarato. Non sostituisce il progetto esecutivo, la verifica legale delle licenze, la valutazione di sicurezza o il collaudo di produzione.</p></section>
    <footer class="report-footer"><span>SRH Private AI Recommender</span><span>${esc(plan.assessment_id)} · ${reportDate(now)}</span></footer>`;
  $('#save-report-pdf').disabled = false;
  $('#download-session').disabled = false;
  $('#consulting-report').classList.remove('report-empty');
  $('#consulting-report').scrollIntoView({behavior: 'smooth', block: 'start'});
}

function openReportComposer() {
  if (!latestPlan) {
    $('#error').textContent = 'Calcola prima la shortlist del cliente.';
    $('#error').classList.remove('hidden');
    return;
  }
  $('#report-workflow').classList.remove('hidden');
  buildConsultingReport();
  $('#report-workflow').scrollIntoView({behavior: 'smooth'});
}

function printConsultingReport() {
  if (!latestPlan || $('#consulting-report').classList.contains('report-empty')) buildConsultingReport();
  const previousTitle = document.title;
  document.title = `${latestPlan.assessment_id}-SRH-private-AI-assessment`;
  document.body.classList.add('report-print-mode');
  const cleanup = () => { document.body.classList.remove('report-print-mode'); document.title = previousTitle; };
  window.addEventListener('afterprint', cleanup, {once: true});
  window.print();
  window.setTimeout(cleanup, 1500);
}

document.querySelectorAll('[data-workflow-target]').forEach(button => button.addEventListener('click', () => {
  const target = document.querySelector(button.dataset.workflowTarget);
  if (target && !target.classList.contains('hidden')) target.scrollIntoView({behavior: 'smooth', block: 'start'});
}));
$('#compose-report').addEventListener('click', () => { try { buildConsultingReport(); } catch (error) { $('#error').textContent = error.message; $('#error').classList.remove('hidden'); } });
$('#save-report-pdf').addEventListener('click', printConsultingReport);
$('#download-session').addEventListener('click', () => downloadJson(`${latestPlan.assessment_id}.srh-assessment.json`, workflowSnapshot()));
window.addEventListener('beforeprint', () => { if (document.body.classList.contains('report-print-mode')) buildConsultingReport(); });
updateWorkflow();
