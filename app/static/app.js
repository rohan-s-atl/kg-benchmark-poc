const form = document.getElementById('ask-form');
const input = document.getElementById('question-input');
const button = document.getElementById('ask-button');
const messages = document.getElementById('messages');
const template = document.getElementById('turn-template');

const navTabs = document.querySelectorAll('.nav-tab');
const demoTab = document.getElementById('demo-tab');
const reportTab = document.getElementById('report-tab');
const reportFrame = document.getElementById('report-frame');
const siteHeader = document.getElementById('site-header');
const headerToggle = document.getElementById('header-toggle');

// Manual collapse toggle (blinds-style pull) instead of scroll-linked —
// a scroll-driven version fought with the report iframe's ResizeObserver
// and made scrolling feel heavy/resistant.
headerToggle.addEventListener('click', () => {
  const collapsed = siteHeader.classList.toggle('collapsed');
  headerToggle.setAttribute('aria-expanded', String(!collapsed));
  headerToggle.setAttribute('aria-label', collapsed ? 'Expand header' : 'Collapse header');
});

// The report iframe is same-origin (served by this app), so we can read its
// content height directly and size the iframe to match — the outer page
// scrolls as one piece instead of the iframe scrolling internally.
// A one-shot measurement at 'load' races the report's own async layout work
// (e.g. Chart.js sizing its canvases after initial paint), so watch for
// height changes with ResizeObserver instead of trusting a single reading.
let reportResizeObserver = null;

reportFrame.addEventListener('load', () => {
  try {
    const doc = reportFrame.contentDocument;
    const resize = () => {
      reportFrame.style.height = `${doc.documentElement.scrollHeight}px`;
    };
    resize();

    if (reportResizeObserver) reportResizeObserver.disconnect();
    reportResizeObserver = new ResizeObserver(resize);
    reportResizeObserver.observe(doc.documentElement);
  } catch (e) {
    reportFrame.style.height = '80vh';
  }
});

navTabs.forEach((tab) => {
  tab.addEventListener('click', () => {
    navTabs.forEach((t) => t.classList.remove('active'));
    tab.classList.add('active');

    if (tab.dataset.tab === 'report') {
      demoTab.hidden = true;
      reportTab.hidden = false;
      // Reload each time so a benchmark re-run between tab switches shows fresh numbers.
      reportFrame.src = `/report?t=${Date.now()}`;
    } else {
      demoTab.hidden = false;
      reportTab.hidden = true;
    }
  });
});

form.addEventListener('submit', (e) => {
  e.preventDefault();
  const question = input.value.trim();
  if (!question) return;
  input.value = '';
  askQuestion(question);
});

document.querySelectorAll('.example-chip').forEach((chip) => {
  chip.addEventListener('click', () => {
    const question = chip.dataset.q;
    askQuestion(question);
  });
});

async function askQuestion(question) {
  button.disabled = true;

  const turn = template.content.firstElementChild.cloneNode(true);
  turn.querySelector('.question-bubble').textContent = question;
  const answerText = turn.querySelector('.answer-text');
  const answerStatus = turn.querySelector('.answer-status');
  const comparePanel = turn.querySelector('.compare-panel');
  const neo4jCol = turn.querySelector('.neo4j-col');
  const neptuneCol = turn.querySelector('.neptune-col');
  const cypherCode = turn.querySelector('.cypher-block code');
  const sparqlCode = turn.querySelector('.sparql-block code');
  const neo4jBadge = turn.querySelector('.neo4j-col .latency-badge');
  const neptuneBadge = turn.querySelector('.neptune-col .latency-badge');

  answerStatus.textContent = 'Generating queries…';
  messages.appendChild(turn);
  turn.scrollIntoView({ behavior: 'smooth', block: 'end' });

  try {
    const response = await fetch('/api/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question }),
    });

    if (!response.ok || !response.body) {
      throw new Error(`Request failed with status ${response.status}`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let newlineIndex;
      while ((newlineIndex = buffer.indexOf('\n')) !== -1) {
        const line = buffer.slice(0, newlineIndex);
        buffer = buffer.slice(newlineIndex + 1);
        if (!line.trim()) continue;
        handleEvent(JSON.parse(line));
      }
    }
  } catch (err) {
    answerStatus.textContent = `Error: ${err.message}`;
    answerStatus.classList.add('error');
  } finally {
    button.disabled = false;
  }

  function handleEvent(event) {
    if (event.type === 'queries') {
      cypherCode.textContent = event.cypher;
      sparqlCode.textContent = event.sparql;
      comparePanel.hidden = false;
      answerStatus.textContent = 'Running queries against Neo4j and Neptune…';
    } else if (event.type === 'waiting_neptune') {
      answerStatus.textContent = `Waiting on Neptune relay… ${event.elapsed_s}s (make sure the notebook relay worker is running)`;
    } else if (event.type === 'latency') {
      neo4jBadge.textContent = event.neo4j_error ? 'error' : `${event.neo4j_ms.toFixed(1)} ms`;
      neptuneBadge.textContent = event.neptune_error ? 'error' : `${event.neptune_ms.toFixed(1)} ms`;

      if (!event.neo4j_error && !event.neptune_error) {
        if (event.neo4j_ms < event.neptune_ms) {
          neo4jCol.classList.add('faster');
        } else if (event.neptune_ms < event.neo4j_ms) {
          neptuneCol.classList.add('faster');
        }
      }

      const errors = [];
      if (event.neo4j_error) errors.push(`Neo4j: ${event.neo4j_error}`);
      if (event.neptune_error) errors.push(`Neptune: ${event.neptune_error}`);
      answerStatus.textContent = errors.length ? errors.join(' | ') : 'Answering…';
      answerStatus.classList.toggle('error', errors.length > 0);
    } else if (event.type === 'token') {
      answerText.textContent += event.text;
    } else if (event.type === 'error') {
      answerStatus.textContent = event.message;
      answerStatus.classList.add('error');
    } else if (event.type === 'done') {
      if (!answerStatus.classList.contains('error')) {
        answerStatus.textContent = '';
      }
    }
    turn.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }
}
