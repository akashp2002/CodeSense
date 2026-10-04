import { useState, useRef, useEffect } from 'react';
import ReactMarkdown from 'react-markdown';
import './index.css';

interface Message {
  id: string;
  role: 'user' | 'ai' | 'system';
  content: string;
  diff?: string;
}

const HINT_QUERIES = [
  "Where is get_db_session defined?",
  "What depends on UserModel?",
  "Rename create_user to register_user",
];

function App() {
  const [repoUrl, setRepoUrl] = useState('https://github.com/akashp2002/demo');
  const [query, setQuery] = useState('');
  const [messages, setMessages] = useState<Message[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isCloning, setIsCloning] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [repoReady, setRepoReady] = useState(false);
  const [theme, setTheme] = useState<'light' | 'dark'>(() => {
    const saved = localStorage.getItem('theme');
    return (saved as 'light' | 'dark') || 'dark';
  });
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('theme', theme);
  }, [theme]);

  const toggleTheme = () => setTheme(t => (t === 'dark' ? 'light' : 'dark'));

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // ─── API Handlers ───
  const handleClone = async () => {
    if (!repoUrl) return;
    setIsCloning(true);
    addMessage('system', 'Cloning repository…');

    try {
      const res = await fetch('http://localhost:8001/clone', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ github_url: repoUrl }),
      });
      const data = await res.json();
      if (res.ok) {
        addMessage('system', `Repository cloned successfully. Ready for analysis.`);
        setRepoReady(true);
      } else {
        addMessage('system', `Clone failed: ${data.detail || 'Unknown error'}`);
      }
    } catch (err: any) {
      addMessage('system', `Network error: ${err.message}`);
    } finally {
      setIsCloning(false);
    }
  };

  const handleDeleteRepo = async () => {
    if (!repoUrl) return;
    setIsDeleting(true);
    const repoName = repoUrl.split('/').pop()?.replace('.git', '') || '';

    try {
      const res = await fetch('http://localhost:8001/delete-repository', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ repo_path: `repos/${repoName}` }),
      });
      if (res.ok) {
        addMessage('system', `Repository "${repoName}" removed.`);
        setRepoReady(false);
      } else {
        const data = await res.json();
        addMessage('system', `Delete failed: ${data.detail || 'Unknown error'}`);
      }
    } catch (err: any) {
      addMessage('system', `Network error: ${err.message}`);
    } finally {
      setIsDeleting(false);
    }
  };

  const handleQuery = (overrideQuery?: string) => {
    const q = overrideQuery ?? query;
    if (!q.trim() || isLoading) return;

    const userMsgId = uid();
    const statusMsgId = uid();
    setMessages(prev => [
      ...prev,
      { id: userMsgId, role: 'user', content: q },
      { id: statusMsgId, role: 'system', content: 'Thinking…' },
    ]);
    if (!overrideQuery) setQuery('');
    setIsLoading(true);

    const ws = new WebSocket('ws://localhost:8001/ws/query');

    ws.onopen = () => ws.send(JSON.stringify({ question: q }));

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);

      if (data.type === 'status' || data.type === 'heartbeat') {
        setMessages(prev =>
          prev.map(m => (m.id === statusMsgId ? { ...m, content: data.message } : m))
        );
      } else if (data.type === 'result') {
        setMessages(prev => prev.filter(m => m.id !== statusMsgId));
        addMessage('ai', data.answer, data.diff_data);
        ws.close();
        setIsLoading(false);
      } else if (data.type === 'error') {
        setMessages(prev => prev.filter(m => m.id !== statusMsgId));
        addMessage('system', `Error: ${data.message}`);
        ws.close();
        setIsLoading(false);
      }
    };

    ws.onerror = () => {
      setMessages(prev => prev.filter(m => m.id !== statusMsgId));
      addMessage('system', 'WebSocket connection failed. Is the backend running?');
      setIsLoading(false);
    };
  };

  const handleApprovePR = async (prompt: string) => {
    addMessage('system', 'Creating pull request…');
    try {
      const repoName = repoUrl.split('/').pop() || 'demo';
      const res = await fetch('http://localhost:8001/create-pr', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt, repo_name: repoName }),
      });
      const data = await res.json();
      if (res.ok) {
        addMessage('system', data.message);
      } else {
        addMessage('system', `PR creation failed: ${data.detail || 'Unknown error'}`);
      }
    } catch (err: any) {
      addMessage('system', `Network error: ${err.message}`);
    }
  };

  // ─── Helpers ───
  const uid = () => `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
  const addMessage = (role: Message['role'], content: string, diff?: string) => {
    setMessages(prev => [...prev, { id: uid(), role, content, diff }]);
  };

  // ─── Render ───
  return (
    <div className="app-container">
      {/* ─── Sidebar ─── */}
      <aside className="sidebar">
        <div className="sidebar-header">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div className="sidebar-logo">
              <div className="logo-icon">CS</div>
              <h1>CodeSense</h1>
            </div>
            <button 
              onClick={toggleTheme} 
              className="btn btn-ghost" 
              style={{ padding: '0.25rem 0.5rem', flex: 'none', fontSize: '1rem' }}
              title="Toggle Theme"
            >
              {theme === 'dark' ? '☀️' : '🌙'}
            </button>
          </div>
          <p className="sidebar-subtitle">AI-powered codebase analysis & refactoring</p>
        </div>

        <div className="sidebar-section">
          <p className="sidebar-section-title">Repository</p>
          <div className="repo-input-group">
            <input
              type="text"
              value={repoUrl}
              onChange={(e) => setRepoUrl(e.target.value)}
              placeholder="https://github.com/user/repo"
              disabled={isCloning}
              spellCheck={false}
            />
            <div className="repo-actions">
              <button
                className="btn btn-primary"
                onClick={handleClone}
                disabled={isCloning || isDeleting}
              >
                {isCloning ? '⏳ Cloning…' : '↓ Clone'}
              </button>
              <button
                className="btn btn-danger"
                onClick={handleDeleteRepo}
                disabled={isCloning || isDeleting || !repoUrl}
              >
                {isDeleting ? '⏳' : '✕ Remove'}
              </button>
            </div>
          </div>
        </div>

        <div className="sidebar-status">
          <div className="status-badge">
            <span className="status-dot" />
            <span>{repoReady ? 'Repository indexed' : 'Waiting for repository'}</span>
          </div>
        </div>
      </aside>

      {/* ─── Chat ─── */}
      <main className="chat-area">
        <div className="chat-header">
          <span className="chat-header-icon">💬</span>
          <span className="chat-header-title">Chat</span>
          <span className="chat-header-subtitle">
            {messages.filter(m => m.role === 'user').length} queries
          </span>
        </div>

        <div className="messages">
          {messages.length === 0 ? (
            <div className="empty-state">
              <div className="empty-state-icon">🔍</div>
              <h3>Ask anything about your codebase</h3>
              <p>
                Clone a repository, then ask questions, trace dependencies, or request refactors.
              </p>
              <div className="empty-hints">
                {HINT_QUERIES.map((hint) => (
                  <button
                    key={hint}
                    className="hint-chip"
                    onClick={() => handleQuery(hint)}
                    disabled={isLoading}
                  >
                    {hint}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            messages.map((msg) => (
              <div key={msg.id} className={`message-row ${msg.role}`}>
                <div className="message-avatar">
                  {msg.role === 'user' ? 'U' : msg.role === 'ai' ? 'CS' : '⚙'}
                </div>
                <div className="message-body">
                  <div className="message-content">
                    {msg.role === 'system' && isLoading && msg === messages[messages.length - 1] ? (
                      <>
                        <ReactMarkdown>{msg.content}</ReactMarkdown>
                        <div className="typing-indicator">
                          <span className="typing-dot" />
                          <span className="typing-dot" />
                          <span className="typing-dot" />
                        </div>
                      </>
                    ) : (
                      <ReactMarkdown>{msg.content}</ReactMarkdown>
                    )}
                  </div>

                  {msg.diff && (
                    <div className="diff-container">
                      <div className="diff-header">
                        <span>Proposed Changes</span>
                        <span>diff</span>
                      </div>
                      <div className="diff-block">{msg.diff}</div>
                      <div className="diff-actions">
                        <button
                          className="btn btn-success"
                          onClick={() => handleApprovePR('Apply approved refactor')}
                        >
                          ✓ Approve & Create PR
                        </button>
                        <button
                          className="btn btn-danger"
                          onClick={() =>
                            addMessage('system', 'Refactor rejected. No changes were applied.')
                          }
                        >
                          ✕ Reject
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              </div>
            ))
          )}
          <div ref={messagesEndRef} />
        </div>

        <div className="input-area">
          <div className="input-container">
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleQuery()}
              placeholder="Ask about the codebase…"
              disabled={isLoading}
            />
            <button
              className="send-btn"
              onClick={() => handleQuery()}
              disabled={isLoading || !query.trim()}
              title="Send"
            >
              ↑
            </button>
          </div>
          <p className="input-hint">
            Search · Impact Analysis · Refactoring — powered by CodeSense
          </p>
        </div>
      </main>
    </div>
  );
}

export default App;
