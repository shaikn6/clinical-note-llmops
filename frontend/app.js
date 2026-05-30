/**
 * Clinical Note LLMOps — Frontend JavaScript
 * Calls FastAPI backend to process clinical notes through the pipeline.
 */

const API_BASE = window.location.origin;

const SAMPLE_NOTES = [
  {
    note_id: "N001",
    note_type: "discharge_summary",
    note_text: "Patient John Smith, DOB 03/15/1965, MRN 789234. Chief complaint: chest pain radiating to left arm for 2 hours. Vital signs: BP 158/94, HR 102, O2 sat 96%. EKG shows ST-elevation. Assessment: Acute myocardial infarction (ICD-10: I21.9). Medications: Aspirin 325mg PO stat, Metoprolol 25mg PO BID, Atorvastatin 40mg PO QHS. Follow-up with cardiology in 1 week. Contact: 555-234-8901."
  },
  {
    note_id: "N002",
    note_type: "outpatient_visit",
    note_text: "Patient Maria Gonzalez, DOB 07/22/1978, MRN 456812. Email: mgonzalez@email.com. Presenting with polyuria, polydipsia, fatigue for 3 weeks. HbA1c 9.2%, fasting glucose 287 mg/dL. Assessment: Type 2 diabetes mellitus uncontrolled (ICD-10: E11.65). Medications started: Metformin 1000mg PO BID with meals, Empagliflozin 10mg PO daily."
  },
  {
    note_id: "N003",
    note_type: "outpatient_visit",
    note_text: "Patient Robert Chen, DOB 11/08/1952, MRN 334521. SSN: 123-45-6789. BP readings: 172/106, 168/102. Assessment: Essential hypertension, stage 2 (ICD-10: I10). Medications: Amlodipine 10mg PO daily, Hydrochlorothiazide 25mg PO daily, Losartan 100mg PO daily."
  },
  {
    note_id: "N004",
    note_type: "emergency_note",
    note_text: "Patient Sarah Thompson, DOB 05/30/1990, MRN 667123. Productive cough x 5 days, fever 101.8F. CXR: right lower lobe consolidation. Assessment: Community-acquired pneumonia (ICD-10: J18.9). Medications: Azithromycin 500mg PO daily x 5 days, Albuterol MDI 2 puffs Q4H PRN."
  },
  {
    note_id: "N005",
    note_type: "psychiatry_note",
    note_text: "Patient David Williams, DOB 09/14/1985, MRN 891234. PHQ-9 score 18. Reports anhedonia, insomnia, passive suicidal ideation. Assessment: Major depressive disorder, severe (ICD-10: F32.2). Started: Sertraline 50mg PO daily. Emergency contact: 555-678-9012."
  }
];

function loadSampleNote(index) {
  const note = SAMPLE_NOTES[index];
  if (!note) return;
  document.getElementById('noteId').value = note.note_id;
  document.getElementById('noteType').value = note.note_type;
  document.getElementById('noteText').value = note.note_text;
  clearAlert();
}

async function processNote() {
  const noteId   = document.getElementById('noteId').value.trim();
  const noteText = document.getElementById('noteText').value.trim();
  const noteType = document.getElementById('noteType').value;

  if (!noteId || !noteText) {
    showAlert('error', 'Please provide a Note ID and note text.');
    return;
  }

  setLoading(true);
  clearAlert();
  hideResults();

  try {
    const response = await fetch(`${API_BASE}/api/process-note`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        note_id:   noteId,
        note_text: noteText,
        note_type: noteType,
        department: 'General',
        user_id:   'frontend-user',
      }),
    });

    if (!response.ok) {
      const err = await response.json().catch(() => ({ detail: 'Unknown error' }));
      throw new Error(err.detail || `HTTP ${response.status}`);
    }

    const data = await response.json();
    renderResults(data);
    showAlert('success', `Pipeline complete. ${data.phi_count} PHI instances removed. ${data.icd_codes.length} ICD-10 codes and ${data.medications.length} medications extracted.`);

  } catch (err) {
    showAlert('error', `Pipeline error: ${err.message}. Is the FastAPI server running on port 8000?`);
  } finally {
    setLoading(false);
  }
}

function renderResults(data) {
  // PHI strip
  document.getElementById('phiCount').textContent = data.phi_count;
  const phiTypeStr = Object.entries(data.phi_types || {})
    .map(([k, v]) => `${k}: ${v}`)
    .join(' · ');
  document.getElementById('phiTypes').textContent = phiTypeStr || 'None detected';

  // Scrubbed text with highlighted tokens
  const scrubbed = (data.scrubbed_text || '').replace(
    /\[([A-Z_]+)\]/g,
    '<span class="phi-token">[$1]</span>'
  );
  document.getElementById('scrubbedText').innerHTML = scrubbed;

  // ICD codes
  const icdList = document.getElementById('icdList');
  icdList.innerHTML = '';
  if (data.icd_codes && data.icd_codes.length > 0) {
    data.icd_codes.forEach(icd => {
      icdList.appendChild(createEntityCard(
        'icd',
        '&#127973;',
        icd.code,
        icd.description || '',
        `Source: ${icd.source || 'llm'}`,
        icd.confidence,
        icd.confidence_label,
      ));
    });
  } else {
    icdList.innerHTML = '<p style="color:var(--color-muted);font-size:0.875rem;">No ICD-10 codes extracted.</p>';
  }

  // Medications
  const medList = document.getElementById('medList');
  medList.innerHTML = '';
  if (data.medications && data.medications.length > 0) {
    data.medications.forEach(med => {
      medList.appendChild(createEntityCard(
        'med',
        '&#128138;',
        med.name,
        `${med.dose} · ${med.route} · ${med.frequency}`,
        `Source: ${med.source || 'regex'}`,
        med.confidence,
        med.confidence_label,
      ));
    });
  } else {
    medList.innerHTML = '<p style="color:var(--color-muted);font-size:0.875rem;">No medications extracted.</p>';
  }

  // Review alert
  const reviewAlert = document.getElementById('reviewAlert');
  if (data.review_items_enqueued > 0) {
    reviewAlert.innerHTML = `
      <div class="alert alert-warn" role="alert">
        &#9888; ${data.review_items_enqueued} low-confidence extraction(s) sent to human review queue.
        <a href="/docs#/default/list_review_queue_api_review_queue_get" style="color:inherit;font-weight:700;margin-left:0.5rem;">View Queue &rarr;</a>
      </div>`;
  } else {
    reviewAlert.innerHTML = `
      <div class="alert alert-success" role="alert">
        &#10003; All extractions above confidence threshold — no human review required.
      </div>`;
  }

  // FHIR Bundle (syntax highlighted)
  const fhirJson = JSON.stringify(data.fhir_bundle, null, 2);
  document.getElementById('fhirViewer').innerHTML = syntaxHighlight(fhirJson);

  showResults();
}

function createEntityCard(type, icon, name, meta, source, confidence, label) {
  const card = document.createElement('div');
  card.className = 'entity-card';

  const confPct = Math.round((confidence || 0) * 100);
  const badgeClass = `badge badge-${label || 'LOW'}`;

  card.innerHTML = `
    <div class="entity-icon ${type}">${icon}</div>
    <div class="entity-info">
      <div class="entity-name">${escapeHtml(name)}</div>
      <div class="entity-meta">${escapeHtml(meta)} &nbsp;·&nbsp; ${escapeHtml(source)}</div>
    </div>
    <span class="${badgeClass}">${label || 'LOW'} ${confPct}%</span>
  `;
  return card;
}

function syntaxHighlight(json) {
  return json
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(
      /("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)/g,
      match => {
        let cls = 'json-num';
        if (/^"/.test(match)) {
          cls = /:$/.test(match) ? 'json-key' : 'json-str';
        } else if (/true|false/.test(match)) {
          cls = 'json-bool';
        }
        return `<span class="${cls}">${match}</span>`;
      }
    );
}

function escapeHtml(str) {
  return String(str || '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function showAlert(type, message) {
  const alertArea = document.getElementById('alertArea');
  const icons = { success: '&#10003;', error: '&#10007;', warn: '&#9888;', info: 'ℹ' };
  alertArea.innerHTML = `
    <div class="alert alert-${type}" role="alert">
      <span>${icons[type] || ''}</span>
      <span>${escapeHtml(message)}</span>
    </div>`;
  alertArea.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function clearAlert() {
  document.getElementById('alertArea').innerHTML = '';
}

function showResults() {
  document.getElementById('resultsPlaceholder').style.display = 'none';
  document.getElementById('resultsPanel').classList.add('visible');
}

function hideResults() {
  document.getElementById('resultsPlaceholder').style.display = '';
  document.getElementById('resultsPanel').classList.remove('visible');
}

function setLoading(loading) {
  const btn   = document.getElementById('processBtn');
  const icon  = document.getElementById('btnIcon');
  const label = document.getElementById('btnLabel');
  btn.disabled = loading;
  if (loading) {
    icon.outerHTML = '<span id="btnIcon" class="loading-spinner"></span>';
    label.textContent = 'Processing…';
  } else {
    document.getElementById('btnIcon').outerHTML = '<span id="btnIcon">&#9658;</span>';
    label.textContent = 'Process Note';
  }
}
