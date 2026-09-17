let catalogs=null;let latestPlan=null;let latestRecommendation=null;let measuredDemo=null;let runtimeChoices=new Map();let localBenchmarkRuns=[];let referenceSimulationRuns=[];const $=s=>document.querySelector(s);const all=s=>[...document.querySelectorAll(s)];
const esc=v=>String(v).replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
const num=(form,name)=>Number(form.elements[name].value);
const range=m=>`${Math.round(m.low).toLocaleString('it-IT')}–${Math.round(m.high).toLocaleString('it-IT')}`;
const labels={STRONG_FIT:'Priorità alta per il benchmark',CONDITIONAL_FIT:'Da provare, con rischio',BORDERLINE_FIT:'Priorità bassa per il benchmark',UNLIKELY_FIT:'Poco probabile rispetto ai target',NOT_FEASIBLE:'Non fattibile'};
const compactRationaleLabels={STRONG_FIT:'Fascia prudente nei target. Confermare con una misura.',CONDITIONAL_FIT:'Stima centrale nei target; l’incertezza richiede una misura.',BORDERLINE_FIT:'Solo la fascia favorevole è nei target; provare se la qualità lo giustifica.',UNLIKELY_FIT:'Anche la fascia favorevole manca almeno un target.',NOT_FEASIBLE:'Un vincolo rigido non è rispettato.'};
const rationaleLabels={STRONG_FIT:'La fascia prudente dell’euristica rispetta i target; serve una misura prima di raccomandare.',CONDITIONAL_FIT:'La stima centrale rispetta i target, ma la fascia d’incertezza li attraversa.',BORDERLINE_FIT:'Solo il limite più favorevole della stima rispetta tutti i target.',UNLIKELY_FIT:'Anche la fascia più favorevole manca almeno un target.',NOT_FEASIBLE:'Viola un vincolo rigido di memoria, contesto, precisione o potenza.'};

async function init(){
  catalogs=await fetch('/api/catalog').then(r=>r.json());
  $('#hardware-options').innerHTML=catalogs.hardware.items.map((v,i)=>`<label class="check"><input type="checkbox" name="hardware" value="${esc(v.id)}" checked><span>${esc(v.name)}<small> · ${v.memory_gib} GB · ${v.power_w} W</small></span></label>`).join('');
  $('#model-options').innerHTML=catalogs.models.items.map((v,i)=>`<label class="check"><input type="checkbox" name="model" value="${esc(v.id)}" ${i<3?'checked':''}><span>${esc(v.name)}</span></label>`).join('');
  await refreshRuntimeChoices();
}

function intakeFromForm(){
  const f=$('#intake-form');
  return {schema:'srh.client-intake.v1',id:f.elements.id.value.trim(),name:f.elements.name.value.trim(),description:'Assessment captured in an SRH client meeting.',workload_class:f.elements.workload_class.value,reasoning:'disabled',
    traffic:{total_users:num(f,'total_users'),concurrent_users:num(f,'concurrent_users'),requests_per_day:num(f,'requests_per_day'),peak_requests_per_minute:num(f,'peak_rpm'),burst_factor:2},
    documents:{context_mode:f.elements.context_mode.value,pages:{p50:num(f,'pages_p50'),p95:num(f,'pages_p95'),max:num(f,'pages_max')},retrieved_pages:{p50:num(f,'retrieved_p50'),p95:num(f,'retrieved_p95'),max:num(f,'retrieved_max')},scanned_fraction:num(f,'scanned_percent')/100,shared_prefix_probability:.4,repeated_context_probability:.3},
    responses:{words:{p50:num(f,'words_p50'),p95:num(f,'words_p95'),max:num(f,'words_max')}},
    experience:{first_useful_response_seconds:num(f,'first_seconds'),complete_response_seconds:num(f,'complete_seconds'),error_rate_max:.01},
    quality:{grounding_required:f.elements.grounding.checked,citation_required:f.elements.citations.checked,structured_output_required:true,minimum_score:.95},
    deployment:{data_residency:f.elements.data_residency.value,maximum_device_power_w:num(f,'max_power')},
    exploration:{hardware_ids:all('[name=hardware]:checked').map(v=>v.value),model_ids:all('[name=model]:checked').map(v=>v.value),requested_model_families:f.elements.requested_models.value.split(',').map(v=>v.trim()).filter(Boolean),weight_bits:all('[name=bits]:checked').map(v=>Number(v.value))},
    translation_assumptions:{words_per_page:450,tokens_per_word:1.34,prompt_overhead_tokens:256}};
}

function setForm(v){const f=$('#intake-form');const set=(n,x)=>{if(f.elements[n])f.elements[n].value=x};set('name',v.name);set('id',v.id);set('workload_class',v.workload_class);set('data_residency',v.deployment.data_residency);set('requested_models',(v.exploration.requested_model_families||[]).join(', '));set('context_mode',v.documents.context_mode);set('scanned_percent',v.documents.scanned_fraction*100);set('total_users',v.traffic.total_users);set('concurrent_users',v.traffic.concurrent_users);set('requests_per_day',v.traffic.requests_per_day);set('peak_rpm',v.traffic.peak_requests_per_minute);for(const [prefix,d] of [['pages',v.documents.pages],['retrieved',v.documents.retrieved_pages],['words',v.responses.words]])for(const k of ['p50','p95','max'])set(`${prefix}_${k}`,d[k]);set('first_seconds',v.experience.first_useful_response_seconds);set('complete_seconds',v.experience.complete_response_seconds);set('max_power',v.deployment.maximum_device_power_w);all('[name=hardware]').forEach(x=>x.checked=v.exploration.hardware_ids.includes(x.value));all('[name=model]').forEach(x=>x.checked=v.exploration.model_ids.includes(x.value));all('[name=bits]').forEach(x=>x.checked=v.exploration.weight_bits.includes(Number(x.value)));}

async function refreshRuntimeChoices(){
  const local=await fetch('/api/local-models').then(r=>r.json()).catch(()=>({models:[]}));runtimeChoices=new Map();const groups=[];
  if(local.models.length){groups.push(`<optgroup label="Disponibili ora sul DGX">${local.models.map((v,i)=>{const key=`local-${i}`;runtimeChoices.set(key,v);return `<option value="${key}">${esc(v.label)} · ${esc(v.provider)}</option>`}).join('')}</optgroup>`)}
  groups.push(`<optgroup label="Catalogo di riferimento — non installati">${catalogs.reference_models.items.map((v,i)=>{const key=`reference-${i}`;runtimeChoices.set(key,{...v,kind:'REFERENCE_NOT_INSTALLED'});return `<option value="${key}">${esc(v.name)} · simulazione</option>`}).join('')}</optgroup>`);
  $('#runtime-model-select').innerHTML=groups.join('')||'<option value="">Nessun modello rilevato</option>';renderRuntimeInfo();renderLiveCandidateOptions();
}

function renderLiveCandidateOptions(){
  const local=[...runtimeChoices.entries()].filter(([,v])=>v.kind==='LOCAL_AVAILABLE');
  const box=$('#live-candidate-options');
  if(!local.length){box.innerHTML='<div class="demo-notice"><strong>Nessun modello generativo locale rilevato.</strong><p>Avvia un endpoint OpenAI-compatible su 127.0.0.1:18300/8000 oppure il servizio Ollama su 127.0.0.1:11434.</p></div>';updateComparisonSelection();return}
  box.innerHTML=local.map(([key,v])=>{const detail=[v.parameter_size,v.quantization,v.loaded?'già caricato':'caricato al test'].filter(Boolean).join(' · ');const identity=v.digest?`sha256:${v.digest.slice(0,12)}…`:(v.root||v.runtime_fingerprint);return `<article class="live-candidate" data-runtime-key="${key}"><label class="candidate-select"><input type="checkbox" class="comparison-candidate"><span><strong>${esc(v.label)}</strong><small>${esc(v.provider)} · ${esc(detail||'runtime attivo')}</small><code>${esc(identity)}</code></span></label></article>`}).join('');
  all('.comparison-candidate').forEach(input=>input.addEventListener('change',updateComparisonSelection));updateComparisonSelection();
}

function updateComparisonSelection(){const count=all('.live-candidate .comparison-candidate:checked').length;$('#comparison-selection-summary').textContent=count<2?`${count} selezionato/i. Ne servono almeno 2.`:`${count} candidati: verranno provati in sequenza con lo stesso protocollo.`;$('#run-live-comparison').disabled=count<2}

function renderRuntimeInfo(){const value=runtimeChoices.get($('#runtime-model-select').value);if(!value){$('#runtime-model-info').textContent='Nessun modello selezionato.';return}if(value.kind==='LOCAL_AVAILABLE'){const snapshot=value.digest?`sha256:${value.digest.slice(0,16)}…`:(value.root?value.root.split('/').pop():'identità snapshot non esposta');const context=value.maximum_context_tokens?` · contesto ${Number(value.maximum_context_tokens).toLocaleString('it-IT')} token`:'';const availability=value.loaded?'già caricato':'installato, caricamento al test';$('#runtime-model-info').innerHTML=`<strong>Disponibile e testabile ora</strong><span>${esc(value.provider)} · ${esc(value.endpoint)}${context} · ${esc(availability)}</span><small>Identità runtime: ${esc(snapshot)}</small>`;$('#run-model-test').textContent='Avvia benchmark locale'}else{const benchmarks=(value.published_benchmarks||[]).map(v=>`${esc(v.name)}: <strong>${esc(v.value)}</strong>`).join(' · ')||'Nessun valore numerico pubblicato nel catalogo.';$('#runtime-model-info').innerHTML=`<strong>Riferimento non installato</strong><span>${value.total_parameters_b}B totali / ${value.active_parameters_b}B attivi · contesto ${value.maximum_context_tokens.toLocaleString('it-IT')} · licenza dichiarata ${esc(value.license)}</span><small>${benchmarks} I benchmark del produttore non misurano il caso cliente.</small>`;$('#run-model-test').textContent='Simula capacità del modello'}}

function render(plan){
  latestPlan=plan;localBenchmarkRuns=[];referenceSimulationRuns=[];latestRecommendation=null;$('#local-test-result').innerHTML='';$('#measured-result').innerHTML='';const c=plan.translated_workload_contract;const shortlist=new Set(plan.screening_summary.shortlist_candidate_ids);
  $('#translated').innerHTML=[['Utenti simultanei',Math.max(...c.traffic.concurrent_users)],['Input impegnativo',`${c.request_profile.input_tokens.p95.toLocaleString('it-IT')} token`],['Risposta impegnativa',`${c.request_profile.output_tokens.p95.toLocaleString('it-IT')} token`],['Tempo massimo richiesto',`${c.service_objectives.end_to_end_p95_ms/1000} secondi`]].map(([l,v])=>`<div class="stat"><strong>${esc(v)}</strong><span>${esc(l)}</span></div>`).join('');
  const rows=plan.candidates.filter(v=>shortlist.has(v.candidate_id));const seconds=m=>`${(m.low/1000).toLocaleString('it-IT',{maximumFractionDigits:1})}–${(m.high/1000).toLocaleString('it-IT',{maximumFractionDigits:1})}`;
  const candidateRow=v=>{const m=v.performance_projection.metrics,cap=v.capacity,uncertainty=Math.round(v.performance_projection.uncertainty_fraction*100),anchored=v.performance_projection.projection_method==='VENDOR_INTERACTIVE_ANCHOR_SCALED',decodeMethod=anchored?'decode ancorato a benchmark interattivo NVIDIA e scalato per dimensione':'decode da tetto di banda con efficienza prudenziale',reason=compactRationaleLabels[v.screening_status],cls=v.screening_status==='STRONG_FIT'?'potential':v.screening_status==='NOT_FEASIBLE'?'blocked':v.screening_status==='CONDITIONAL_FIT'?'conditional':'unlikely';return `<tr><td data-label="Ambiente"><strong>${esc(v.hardware.name)}</strong><br><small>${v.hardware.power_w} W · ${esc(v.hardware.class)}</small></td><td data-label="Classe modello">${esc(v.model.name)}<br><small>${v.weight_bits} bit · ${esc(v.model.architecture)}</small></td><td data-label="Memoria">${cap.estimated_total_required_gib} / ${cap.usable_memory_gib} GiB<br><small>${cap.estimated_headroom_gib} GiB margine</small></td><td data-label="Avvio sotto carico" class="metric-range">${seconds(m.ttfa_p95_ms)} s<small>${c.request_profile.input_tokens.p95.toLocaleString('it-IT')} token in ingresso · ${Math.max(...c.traffic.concurrent_users)} utenti</small><small>Base: prefill SRH prudenziale</small></td><td data-label="Risposta impegnativa" class="metric-range">${seconds(m.end_to_end_p95_ms)} s<small>${c.request_profile.output_tokens.p95.toLocaleString('it-IT')} token a ${range(m.answer_tokens_per_second_per_user)} tok/s per utente · ±${uncertainty}%</small><small>Base: ${decodeMethod}</small></td><td data-label="Priorità di prova" class="priority-cell"><span class="badge ${cls}">${labels[v.screening_status]}</span><small>${esc(reason)}</small></td></tr>`};
  const table=values=>`<div class="comparison-table-scroll" role="region" aria-label="Confronto configurazioni" tabindex="0"><table class="comparison-table"><thead><tr><th>Ambiente</th><th>Classe modello</th><th>Memoria</th><th>Avvio sotto carico</th><th>Completamento della risposta impegnativa</th><th>Priorità di prova</th></tr></thead><tbody>${values.map(candidateRow).join('')}</tbody></table></div>`;
  $('#shortlist').innerHTML=`<p class="result-explainer"><strong>${rows.length} candidati da portare al benchmark.</strong> I tempi di completamento si riferiscono a ${c.request_profile.output_tokens.p95.toLocaleString('it-IT')} token di uscita (circa ${Math.round(c.request_profile.output_tokens.p95/1.34).toLocaleString('it-IT')} parole) con ${Math.max(...c.traffic.concurrent_users)} utenti simultanei. Una risposta breve termina molto prima.</p>${table(rows)}`;
  const selectedModels=plan.client_inputs.exploration.model_ids;
  const coverage=selectedModels.map(id=>{const values=plan.candidates.filter(v=>v.model.id===id),feasible=values.filter(v=>v.screening_status!=='NOT_FEASIBLE'),picked=values.filter(v=>shortlist.has(v.candidate_id));return {name:values[0]?.model.name||id,total:values.length,feasible:feasible.length,picked:picked.length,best:feasible[0]||values[0]}});
  $('#model-coverage').innerHTML=`<h3>Copertura delle classi selezionate</h3><div class="coverage-grid">${coverage.map(v=>`<div class="coverage-card"><strong>${esc(v.name)}</strong><span>${v.feasible?v.picked+' candidato/i in shortlist':"Nessuna combinazione fattibile"}</span><small>${v.feasible?`${v.feasible} combinazioni superano capacità, contesto e potenza`:`Migliore tentativo: ${esc(v.best?.blockers.join(', ')||'non disponibile')}`}</small></div>`).join('')}</div>`;
  $('#candidate-matrix').innerHTML=table(plan.candidates);
  const ex=plan.benchmark_handoff.experiment_plan.experiments;
  const purpose={"baseline-p50-c1":"Misura la risposta tipica con un utente.","tail-p95-c1":"Misura un documento impegnativo con un utente.","max-envelope-c1":"Verifica il massimo dichiarato.","cold-p95-c1":"Misura il caso senza riuso della cache."};
  $('#test-plan').innerHTML=`<p><strong>${ex.length} prove</strong> per ciascun candidato selezionato:</p><ol>${ex.slice(0,6).map(v=>`<li>${esc(purpose[v.experiment_id]||(v.experiment_id.startsWith('concurrency-')?'Misura il carico con più utenti contemporanei.':'Misura il riuso di documenti e contesti ripetuti.'))} <small>(${v.request.input_tokens.toLocaleString('it-IT')} in / ${v.request.output_tokens.toLocaleString('it-IT')} out, ${v.concurrency} ${v.concurrency===1?'utente':'utenti'})</small></li>`).join('')}${ex.length>6?`<li>+ ${ex.length-6} ulteriori condizioni di coda e riuso</li>`:''}</ol>`;
  const families=plan.benchmark_handoff.requested_model_families||[];
  $('#assumptions').innerHTML=`<ul><li>Token derivati da pagine e parole dichiarate.</li><li>${Math.round(plan.client_inputs.documents.scanned_fraction*100)}% dei documenti dichiarati come scansioni.</li><li>Famiglie richieste: ${families.length?families.map(esc).join(', '):'da definire'}.</li><li>Prestazioni H100 dense: benchmark interattivo NVIDIA scalato; altre combinazioni: roofline SRH a bassa confidenza.</li><li>Qualità, OCR e retrieval non sono simulati.</li><li>${plan.historical_evidence_coverage.length} evidenza storica SRH pertinente all’hardware selezionato.</li><li>Esito finale solo dopo benchmark osservato.</li></ul>`;
  $('#local-concurrency').value=Math.min(8,Math.max(...c.traffic.concurrent_users));
  $('#comparison-concurrency').value=Math.min(8,Math.max(...c.traffic.concurrent_users));
  $('#results').classList.remove('hidden');workflowPlanUpdated();$('#results').scrollIntoView({behavior:'smooth'});
}

$('#intake-form').addEventListener('submit',async e=>{e.preventDefault();$('#error').classList.add('hidden');try{const response=await fetch('/api/plan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(intakeFromForm())});const data=await response.json();if(!response.ok)throw new Error(data.error||'Errore di pianificazione');render(data)}catch(err){$('#error').textContent=err.message;$('#error').classList.remove('hidden')}});
$('#load-example').addEventListener('click',async()=>setForm(await fetch('/api/example').then(r=>r.json())));
$('#download-json').addEventListener('click',()=>{if(!latestPlan)return;const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(latestPlan,null,2)],{type:'application/json'}));a.download=`${latestPlan.assessment_id}.capacity-plan.json`;a.click();URL.revokeObjectURL(a.href)});
$('#print').addEventListener('click',()=>openReportComposer());
$('#runtime-model-select').addEventListener('change',renderRuntimeInfo);
$('#refresh-local-models').addEventListener('click',refreshRuntimeChoices);
$('#refresh-comparison-models').addEventListener('click',refreshRuntimeChoices);
function renderLocalBenchmark(report){
  const s=report.summary,fmt=v=>v==null?'n/d':Number(v).toLocaleString('it-IT',{maximumFractionDigits:2});
  const checkLabels={ttfa_p95:'Avvio della risposta',end_to_end_p95:'Durata della risposta prodotta',answer_speed_min:'Velocità di generazione',error_rate:'Affidabilità delle richieste',quality_minimum:'Correttezza dell’estrazione sintetica'};
  const failed=Object.entries(report.checks).filter(([,v])=>!v.pass);
  const checks=Object.entries(report.checks).map(([name,v])=>`<li class="${v.pass?'pass':'fail'}"><strong>${v.pass?'In linea':'Da ottimizzare'}</strong> · ${esc(checkLabels[name]||name)}: ${fmt(v.actual)} / obiettivo ${fmt(v.target)}</li>`).join('');
  localBenchmarkRuns=localBenchmarkRuns.filter(v=>!(v.model===report.model&&v.profile===report.profile&&v.concurrency===report.concurrency));
  localBenchmarkRuns.push(report);
  workflowEvidenceUpdated();
  const cohort=localBenchmarkRuns.filter(v=>v.profile===report.profile&&v.concurrency===report.concurrency&&v.repetitions===report.repetitions&&v.requested_input_tokens===report.requested_input_tokens);
  const identities=new Set(cohort.map(v=>v.runtime_root||v.model));
  const ranked=cohort.filter(v=>v.all_checks_pass).sort((a,b)=>a.summary.ttfa_seconds.p95-b.summary.ttfa_seconds.p95||a.summary.elapsed_seconds.p95-b.summary.elapsed_seconds.p95||b.summary.answer_tokens_per_second.min-a.summary.answer_tokens_per_second.min);
  const headline=report.all_checks_pass?'Configurazione in linea con tutti gli obiettivi':failed.length===1&&failed[0][0]==='ttfa_p95'?'Risposta corretta e veloce; avvio sotto carico da ottimizzare':`${report.objective_summary.met} obiettivi su ${report.objective_summary.total} sono già in linea`;
  const recommendation=identities.size<2?'Questa misura diventa la baseline reale del DGX Spark. Quando sarà disponibile un secondo checkpoint distinto, lo stesso test consentirà il confronto relativo.':ranked.length?`Nel confronto osservato della sessione, ${ranked[0].model} soddisfa tutti gli obiettivi ed è il candidato guida.`:'Le misure indicano quali parametri ottimizzare prima del confronto finale.';
  const actualOutput=s.answer_tokens?.p95;
  const extrapolated=report.planning_bridge?.generation_seconds_for_contract_p95_output_at_observed_min_rate;
  const bridge=extrapolated==null?'Proiezione non disponibile.':`Alla velocità minima osservata, generare tutti i ${Number(report.contract_p95_output_tokens).toLocaleString('it-IT')} token del caso impegnativo richiederebbe circa ${fmt(extrapolated)} s di sola generazione, oltre al tempo di lettura del prompt.`;
  const comparison=cohort.map(v=>`<tr><td>${esc(v.model)}${v.runtime_root?`<small>${esc(v.runtime_root.split('/').slice(-2).join('/'))}</small>`:''}</td><td>${fmt(v.summary.ttfa_seconds?.p95)} s</td><td>${fmt(v.summary.elapsed_seconds?.p95)} s<br><small>${fmt(v.summary.answer_tokens?.p95)} token prodotti</small></td><td>${fmt(v.summary.answer_tokens_per_second?.min)}</td><td>${v.objective_summary.met}/${v.objective_summary.total} in linea</td></tr>`).join('');
  const config=report.runtime_configuration||{},protocol=config.effective_test_protocol||{},energy=report.energy_observation||{};
  const runtimeDetails=[config.quantization||'quantizzazione n/d',`top-k ${protocol.top_k??'n/d'}`,`KV cache ${config.kv_cache?.compression||'n/d'}`,`speculative decoding ${config.speculative_decoding?.status||'n/d'}`].join(' · ');
  const energyDetails=energy.supported?`${fmt(energy.average_power_w)} W medi, picco ${fmt(energy.peak_power_w)} W, +${fmt(energy.incremental_average_power_w)} W rispetto al baseline e ${fmt(energy.energy_wh)} Wh nella campagna.`:'Telemetria energetica non disponibile.';
  return `<div class="verified"><span>MISURA REALE SUL DGX SPARK</span><strong>${esc(headline)}</strong><p>${report.objective_summary.met} obiettivi su ${report.objective_summary.total} raggiunti nel test di estrazione sintetica.</p></div><div class="stats"><div class="stat"><strong>${fmt(s.ttfa_seconds?.p95)} s</strong><span>Avvio p95 con ${report.concurrency} utenti</span></div><div class="stat"><strong>${fmt(s.elapsed_seconds?.p95)} s</strong><span>Risposta osservata · ${fmt(actualOutput)} token</span></div><div class="stat"><strong>${fmt(s.answer_tokens_per_second?.min)} tok/s</strong><span>Velocità minima osservata</span></div><div class="stat"><strong>${Math.round(s.quality_pass_rate*100)}%</strong><span>Correttezza estrazione sintetica</span></div></div><div class="demo-notice"><strong>Condizioni confrontabili:</strong> target sintetico circa ${Number(report.requested_input_tokens).toLocaleString('it-IT')} token, ${fmt(s.prompt_tokens?.p95)} token effettivamente osservati dal tokenizer, massimo ${Number(report.requested_output_tokens).toLocaleString('it-IT')} in uscita, ${report.concurrency} utenti simultanei, thinking disattivato, ${s.request_count} richieste misurate dopo un warm-up escluso dai KPI. Il modello ha terminato spontaneamente dopo circa ${fmt(actualOutput)} token perché il compito richiedeva un JSON breve.<br><strong>Runtime:</strong> ${esc(runtimeDetails)}.<br><strong>Energia osservata:</strong> ${esc(energyDetails)}</div><ul class="gate-list">${checks}</ul><div class="session-recommendation"><strong>Collegamento con la shortlist</strong><p>${esc(bridge)} Per questo il tempo del Dense 8B nella tabella rappresenta una bozza lunga circa 1.200 parole, non una normale risposta breve. Qwen3.8-Flash-Next è un runtime MoE specifico e non va confrontato direttamente con l’archetipo Dense 8B.</p><strong>${esc(recommendation)}</strong></div><table><thead><tr><th>Configurazione misurata</th><th>Avvio p95</th><th>Risposta realmente prodotta</th><th>tok/s min</th><th>Obiettivi</th></tr></thead><tbody>${comparison}</tbody></table>`;
}

$('#run-model-test').addEventListener('click',async()=>{
  const button=$('#run-model-test');
  try{
    if(!latestPlan)throw new Error('Calcola prima la shortlist del cliente.');
    const choice=runtimeChoices.get($('#runtime-model-select').value);
    if(!choice)throw new Error('Seleziona un modello.');
    button.disabled=true;
    button.textContent=choice.kind==='LOCAL_AVAILABLE'?'Benchmark in corso…':'Simulazione in corso…';
    $('#local-test-result').innerHTML='<div class="running">Elaborazione avviata. Un test locale rappresentativo può richiedere alcuni minuti.</div>';
    let response;
    if(choice.kind==='LOCAL_AVAILABLE'){
      response=await fetch('/api/local-benchmark',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({contract:latestPlan.translated_workload_contract,endpoint:choice.endpoint,model:choice.id,profile:$('#local-test-profile').value,repetitions:Number($('#local-repetitions').value),concurrency:Number($('#local-concurrency').value)})});
    }else{
      response=await fetch('/api/reference-simulation',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({contract:latestPlan.translated_workload_contract,model_id:choice.id,hardware_ids:latestPlan.client_inputs.exploration.hardware_ids,weight_bits:Math.min(...latestPlan.client_inputs.exploration.weight_bits),maximum_power_w:latestPlan.client_inputs.deployment.maximum_device_power_w})});
    }
    const report=await response.json();
    if(!response.ok)throw new Error(report.error||'Valutazione non riuscita');
    if(choice.kind==='LOCAL_AVAILABLE'){
      $('#local-test-result').innerHTML=renderLocalBenchmark(report);
    }else{
      referenceSimulationRuns=referenceSimulationRuns.filter(value=>value.model.id!==report.model.id);
      referenceSimulationRuns.push(report);
      const rows=report.candidates.map(v=>`<tr><td>${esc(v.hardware.name)}</td><td>${v.capacity.estimated_total_required_gib} / ${v.capacity.usable_memory_gib} GiB</td><td>${labels[v.screening_status]}</td><td>${esc(rationaleLabels[v.screening_status])}</td></tr>`).join('');
      const benchmarks=(report.model.published_benchmarks||[]).map(v=>`${esc(v.name)}: <strong>${esc(v.value)}</strong>`).join(' · ')||'Nessun benchmark numerico incluso.';
      $('#local-test-result').innerHTML=`<div class="reference-result"><span>SIMULAZIONE DI RIFERIMENTO — NON MISURATA</span><strong>${esc(report.model.name)}</strong><p>${benchmarks}</p><p>I dati pubblicati riguardano task e infrastrutture differenti. La tabella usa il modello euristico SRH solo per lo screening locale.</p></div><table><thead><tr><th>Hardware</th><th>Memoria richiesta / utile</th><th>Valutazione</th><th>Motivo</th></tr></thead><tbody>${rows}</tbody></table>`;
      workflowEvidenceUpdated();
    }
  }catch(err){
    $('#local-test-result').innerHTML='';$('#error').textContent=err.message;$('#error').classList.remove('hidden');
  }finally{
    button.disabled=false;renderRuntimeInfo();
  }
});

const blockerLabels={ttfa_p95_ms:'prima risposta oltre target',end_to_end_p95_ms:'risposta completa oltre target',answer_tokens_per_second_min:'velocità di generazione insufficiente',error_rate_max:'troppi errori',quality_minimum_score:'qualità sotto soglia',required_quality_checks:'controlli qualità obbligatori',license_review_status:'licenza da revisionare'};
function renderRecommendationResult(report,{isDemo=false}={}){
  const current=currentRecommendationIsForAssessment();
  const decision=report.verdict==='RECOMMENDED'?`Candidato guida: ${report.recommended_label}`:report.verdict==='NO_DEPLOYMENT_MEETS_REQUIREMENTS'?`Nessun candidato supera tutti i gate${report.closest_candidate_label?`; più vicino ai target: ${report.closest_candidate_label}`:''}`:'Confronto preliminare: decisione non ancora emessa';
  const action=isDemo&&!current?'Il pacchetto dimostrativo usa un contratto diverso e resta escluso dal dossier cliente.':report.verdict==='RECOMMENDED'?'La misura vale per lo scenario sintetico comune. Il passo successivo è provarla sui documenti e sulla ground truth del cliente.':report.verdict==='NO_DEPLOYMENT_MEETS_REQUIREMENTS'?'Prova una configurazione diversa o concorda target differenti, senza modificare retroattivamente il protocollo.':report.decision_scope==='LAB_SYNTHETIC_SCENARIO'?'Usa cinque ripetizioni identiche per rendere il confronto decisionale.':'Completa le evidenze e i metadati richiesti dal manifest importato.';
  const value=(v,digits=2)=>v==null?'n/d':Number(v).toLocaleString('it-IT',{maximumFractionDigits:digits});
  const seconds=v=>v==null?'n/d':value(v/1000),percent=v=>v==null?'n/d':value(v*100,0);
  const rows=report.candidates.map((v,index)=>{const config=v.deployment.runtime_configuration||{},energy=v.deployment.energy_observation||{},protocol=config.effective_test_protocol||{};const tuning=[v.deployment.model?.quantization||config.quantization||'quantizzazione n/d',`top-k ${protocol.top_k??'n/d'}`,`KV ${config.kv_cache?.compression||'n/d'}`,`speculative ${config.speculative_decoding?.status||'n/d'}`].join(' · ');const power=energy.supported?`${value(energy.average_power_w)} W medi<small>+${value(energy.incremental_average_power_w)} W sul baseline · ${value(energy.energy_wh,3)} Wh</small>`:'n/d';return `<tr><td data-label="Candidato"><strong>${index===0&&report.ranking?.[0]===v.profile_id?'1 · ':''}${esc(v.deployment.label)}</strong><small>${esc(v.deployment.engine||'runtime')} · ${esc(tuning)}</small></td><td data-label="Avvio p95">${seconds(v.checks.ttfa_p95_ms.actual)} s</td><td data-label="Risposta p95">${seconds(v.checks.end_to_end_p95_ms.actual)} s</td><td data-label="Velocità min.">${value(v.checks.answer_tokens_per_second_min.actual)} tok/s</td><td data-label="Energia">${power}</td><td data-label="Qualità">${percent(v.checks.quality_minimum_score.actual)}%</td><td data-label="Esito">${v.eligible?'<span class="badge potential">Idoneo ai gate</span>':`<span class="badge blocked">Da approfondire</span><small>${esc(v.blocking_objectives.map(x=>blockerLabels[x]||x).join('; '))}</small>`}</td></tr>`}).join('');
  return `<div class="${current?'verified':'demo-notice'}"><span>${report.decision_scope==='LAB_SYNTHETIC_SCENARIO'?'CONFRONTO LOCALE OSSERVATO':'ESITO SU DATI IMPORTATI'}</span><strong>${esc(decision)}</strong><p>${esc(action)}</p></div><div class="comparison-table-scroll" role="region" aria-label="Risultati confronto reale" tabindex="0"><table class="comparison-table measured-comparison"><thead><tr><th>Candidato e runtime</th><th>Avvio p95</th><th>Risposta p95</th><th>Velocità min.</th><th>Energia</th><th>Qualità</th><th>Esito</th></tr></thead><tbody>${rows}</tbody></table></div><p class="result-explainer"><strong>Perimetro:</strong> ${esc(report.comparison_scope)}. ${esc(report.recommended_next_action)}</p>`;
}

$('#run-live-comparison').addEventListener('click',async()=>{
  const button=$('#run-live-comparison');
  try{
    if(!latestPlan)throw new Error('Calcola prima la shortlist del cliente.');
    const selected=all('.live-candidate').filter(card=>card.querySelector('.comparison-candidate').checked).map(card=>{const runtime=runtimeChoices.get(card.dataset.runtimeKey);return {runtime_fingerprint:runtime.runtime_fingerprint,label:runtime.label}});
    if(selected.length<2)throw new Error('Seleziona almeno due candidati locali distinti.');
    button.disabled=true;button.textContent='Confronto in corso…';
    $('#measured-result').innerHTML=`<div class="running"><strong>Campagna avviata su ${selected.length} candidati.</strong><p>I modelli vengono eseguiti in sequenza. Ollama può impiegare tempo per caricare i pesi; non chiudere questa pagina.</p></div>`;
    const response=await fetch('/api/live-comparison',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({contract:latestPlan.translated_workload_contract,candidates:selected,profile:$('#comparison-profile').value,repetitions:Number($('#comparison-repetitions').value),concurrency:Number($('#comparison-concurrency').value),policy:'performance_first'})});
    const report=await response.json();
    if(!response.ok)throw new Error(report.error||'Confronto locale non riuscito');
    latestRecommendation=report;
    for(const row of report.candidates){if(row.observation){localBenchmarkRuns=localBenchmarkRuns.filter(v=>!(v.model===row.observation.model&&v.profile===row.observation.profile&&v.concurrency===row.observation.concurrency));localBenchmarkRuns.push(row.observation)}}
    workflowRecommendationUpdated();
    $('#measured-result').innerHTML=renderRecommendationResult(report);
  }catch(err){$('#measured-result').innerHTML='';$('#error').textContent=err.message;$('#error').classList.remove('hidden')}
  finally{button.textContent='Avvia confronto reale';updateComparisonSelection()}
});

const readJson=file=>file.text().then(text=>JSON.parse(text));
const downloadJson=(name,value)=>{const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));a.download=name;a.click();URL.revokeObjectURL(a.href)};
const selectedEvidenceFiles=()=>all('.evidence-file').flatMap(input=>[...input.files]);
const updateMeasuredFiles=()=>{if(measuredDemo){$('#measured-files').textContent=`Demo caricata: 1 manifest, 1 contratto e ${measuredDemo.evidences.length} evidenze osservate.`;return}const files=selectedEvidenceFiles();$('#measured-files').textContent=files.length?`${files.length} evidenze selezionate: ${files.map(v=>v.name).join(', ')}`:'Nessun pacchetto caricato.'};
all('#manifest-file, #measured-contract-file, .evidence-file').forEach(input=>input.addEventListener('change',()=>{measuredDemo=null;updateMeasuredFiles()}));
$('#load-measured-demo').addEventListener('click',async()=>{try{measuredDemo=await fetch('/api/measured-demo').then(r=>{if(!r.ok)throw new Error('Demo non disponibile');return r.json()});updateMeasuredFiles();$('#measured-result').innerHTML='<div class="demo-notice"><strong>Demo pronta.</strong> Usa “Valuta evidenze misurate”. I tre file provengono dalla precedente campagna SRH su GB10; il contratto è quello del test, non quello del cliente corrente.</div>'}catch(err){$('#error').textContent=err.message;$('#error').classList.remove('hidden')}});
$('#download-import-guide').addEventListener('click',async()=>{try{const guide=await fetch('/api/evidence-import-guide').then(r=>r.json());downloadJson('SRH-evidence-import-guide.json',guide)}catch(err){$('#error').textContent=err.message;$('#error').classList.remove('hidden')}});
$('#download-demo-package').addEventListener('click',async()=>{try{const pack=measuredDemo||await fetch('/api/measured-demo').then(r=>r.json());downloadJson('01-workload-contract.json',pack.contract);downloadJson('02-candidate-manifest.json',pack.manifest);pack.evidences.forEach((item,index)=>downloadJson(`03-evidence-${index+1}.json`,item.evidence))}catch(err){$('#error').textContent=err.message;$('#error').classList.remove('hidden')}});
$('#evaluate-measured').addEventListener('click',async()=>{
  try{
    if(!latestPlan&&!measuredDemo)throw new Error('Calcola prima la shortlist o carica la demo misurata.');
    let contract,manifest,evidences;
    if(measuredDemo){
      ({contract,manifest,evidences}=measuredDemo);
    }else{
      const contractFile=$('#measured-contract-file').files[0],manifestFile=$('#manifest-file').files[0],evidenceFiles=selectedEvidenceFiles();
      if(!manifestFile||evidenceFiles.length<2)throw new Error('Seleziona un manifest e almeno due file di evidenza nei campi separati.');
      contract=contractFile?await readJson(contractFile):latestPlan.translated_workload_contract;
      manifest=await readJson(manifestFile);
      evidences=await Promise.all(evidenceFiles.map(async file=>({source:file.name,evidence:await readJson(file)})));
    }
    const response=await fetch('/api/deployment-recommendation',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({contract,manifest,evidences})});
    const report=await response.json();
    if(!response.ok)throw new Error(report.error||'Evidenze non valutabili');
    latestRecommendation=report;
    workflowRecommendationUpdated();
    const current=currentRecommendationIsForAssessment();
    const label=!current?'Demo misurata separata dal caso cliente':report.verdict==='RECOMMENDED'?'Configurazione raccomandata':report.verdict==='NO_DEPLOYMENT_MEETS_REQUIREMENTS'?'Misure valide: serve una nuova configurazione':'Confronto non ancora conclusivo';
    const action=!current?'Il contratto della demo è diverso da quello del cliente corrente. Il risultato viene mostrato come esempio e non entra nel report decisionale.':report.verdict==='RECOMMENDED'?`La configurazione ${report.recommended_label} supera qualità e obiettivi; il prossimo passo è il test di accettazione sul sito cliente.`:report.verdict==='NO_DEPLOYMENT_MEETS_REQUIREMENTS'?'La qualità è stata verificata, ma nessun candidato supera tutti gli obiettivi di servizio. Modifica la shortlist o concorda un target diverso e ripeti il benchmark.':'Completa le evidenze mancanti o rendi identici scenario, evaluator, esperimento e protocollo.';
    $('#measured-result').innerHTML=renderRecommendationResult(report,{isDemo:!current});
  }catch(err){
    $('#error').textContent=err.message;$('#error').classList.remove('hidden');
  }
});
init().catch(err=>{const box=$('#error');box.textContent=`Impossibile caricare i cataloghi: ${err.message}`;box.classList.remove('hidden')});
