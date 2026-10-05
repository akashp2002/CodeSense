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
  const [authToken, setAuthToken] = useState(localStorage.getItem('authToken') || '');
  const [authEmail, setAuthEmail] = useState('');
  const [authPassword, setAuthPassword] = useState('');
  const [authLoading, setAuthLoading] = useState(false);
  const [authError, setAuthError] = useState('');
  const [isRegistering, setIsRegistering] = useState(false);
  const [theme, setTheme] = useState<'light' | 'dark'>(() => {
    const saved = localStorage.getItem('theme');
    return (saved as 'light' | 'dark') || 'dark';
  });
  const [userId, setUserId] = useState<string>('');
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Helper to extract user_id from JWT
  const getUserIdFromToken = (token: string): string => {
    try {
      const payload = JSON.parse(atob(token.split('.')[1]));
      return payload.sub || '';
    } catch {
      return '';
    }
  };

  // Restore userId from stored token on mount
  useEffect(() => {
    if (authToken) {
      setUserId(getUserIdFromToken(authToken));
    }
  }, []);

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('theme', theme);
  }, [theme]);

  const toggleTheme = () => setTheme(t => (t === 'dark' ? 'light' : 'dark'));

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    
    // Save history whenever messages change
    if (authToken && messages.length > 0) {
      fetch('http://localhost:8001/history', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${authToken}`
        },
        body: JSON.stringify({ history: JSON.stringify(messages) })
      }).catch(console.error);
    }
  }, [messages, authToken]);

  // Load history on login/mount
  useEffect(() => {
    if (authToken) {
      fetch('http://localhost:8001/history', {
        headers: { 'Authorization': `Bearer ${authToken}` }
      })
      .then(res => res.json())
      .then(data => {
        if (data.history && data.history !== '[]') {
          setMessages(JSON.parse(data.history));
        }
      })
      .catch(console.error);
    } else {
      setMessages([]);
    }
  }, [authToken]);

  // ─── Auth Handlers ───
  const handleAuth = async (e: React.FormEvent) => {
    e.preventDefault();
    setAuthLoading(true);
    setAuthError('');
    try {
      const formData = new URLSearchParams();
      formData.append('username', authEmail);
      formData.append('password', authPassword);
      
      const endpoint = isRegistering ? 'http://localhost:8001/register' : 'http://localhost:8001/login';
      const res = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: formData
      });
      const data = await res.json();
      if (res.ok) {
        setAuthToken(data.access_token);
        setUserId(getUserIdFromToken(data.access_token));
        localStorage.setItem('authToken', data.access_token);
      } else {
        setAuthError(data.detail || (isRegistering ? 'Registration failed' : 'Login failed'));
      }
    } catch (err) {
      setAuthError('Network error connecting to server');
    }
    setAuthLoading(false);
  };

  const handleGuest = async () => {
    setAuthLoading(true);
    setAuthError('');
    try {
      const res = await fetch('http://localhost:8001/guest', { method: 'POST' });
      const data = await res.json();
      if (res.ok) {
        setAuthToken(data.access_token);
        setUserId(getUserIdFromToken(data.access_token));
        localStorage.setItem('authToken', data.access_token);
      } else {
        setAuthError('Failed to start guest session');
      }
    } catch (err) {
      setAuthError('Network error connecting to server');
    }
    setAuthLoading(false);
  };

  const handleLogout = () => {
    setAuthToken('');
    setUserId('');
    localStorage.removeItem('authToken');
    setMessages([]);
    setRepoReady(false);
  };

  // ─── API Handlers ───
  const handleClone = async () => {
    if (!repoUrl) return;
    setIsCloning(true);
    addMessage('system', 'Cloning repository…');

    try {
      const res = await fetch('http://localhost:8001/clone', {
        method: 'POST',
        headers: { 
          'Content-Type': 'application/json',
          ...(authToken ? { 'Authorization': `Bearer ${authToken}` } : {})
        },
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
        headers: { 
          'Content-Type': 'application/json',
          ...(authToken ? { 'Authorization': `Bearer ${authToken}` } : {})
        },
        body: JSON.stringify({ repo_path: `repos/${userId}/${repoName}` }),
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

    const wsUrl = authToken 
      ? `ws://localhost:8001/ws/query?token=${encodeURIComponent(authToken)}`
      : 'ws://localhost:8001/ws/query';
    const ws = new WebSocket(wsUrl);

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
        headers: { 
          'Content-Type': 'application/json',
          ...(authToken ? { 'Authorization': `Bearer ${authToken}` } : {})
        },
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

        {authToken && (
          <div className="sidebar-section" style={{ marginTop: 'auto', borderTop: '1px solid var(--border-subtle)', paddingTop: '1rem' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div style={{ display: 'flex', flexDirection: 'column' }}>
                <span style={{ fontSize: '0.75rem', color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.5px', fontWeight: 600 }}>Account</span>
                <span style={{ fontSize: '0.85rem', color: 'var(--text-primary)', marginTop: '2px' }}>Active Session</span>
              </div>
              <button 
                onClick={handleLogout} 
                className="btn btn-secondary" 
                style={{ padding: '0.4rem 0.8rem', fontSize: '0.8rem', borderRadius: 'var(--radius-sm)', border: '1px solid var(--border-subtle)' }}
              >
                Sign Out
              </button>
            </div>
          </div>
        )}

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

      {/* ─── Auth Modal ─── */}
      {!authToken && (
        <div className="auth-overlay">
          <div className="auth-modal">
            <div className="auth-header" style={{ textAlign: 'center', marginBottom: '0.5rem' }}>
              <h2 style={{ fontSize: '1.75rem', letterSpacing: '-0.5px' }}>CodeSense</h2>
              <p style={{ marginTop: '0.25rem' }}>{isRegistering ? 'Create a new account' : 'Welcome back, sign in to continue'}</p>
            </div>
            
            <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '1rem', background: 'var(--bg-elevated)', padding: '0.25rem', borderRadius: 'var(--radius-md)' }}>
              <button 
                type="button" 
                onClick={() => {setIsRegistering(false); setAuthError('');}}
                style={{ flex: 1, padding: '0.5rem', borderRadius: 'var(--radius-sm)', border: 'none', background: !isRegistering ? 'var(--bg-surface)' : 'transparent', color: !isRegistering ? 'var(--text-primary)' : 'var(--text-secondary)', boxShadow: !isRegistering ? '0 1px 3px rgba(0,0,0,0.1)' : 'none', cursor: 'pointer', fontWeight: 500, transition: 'all 0.2s' }}
              >
                Sign In
              </button>
              <button 
                type="button" 
                onClick={() => {setIsRegistering(true); setAuthError('');}}
                style={{ flex: 1, padding: '0.5rem', borderRadius: 'var(--radius-sm)', border: 'none', background: isRegistering ? 'var(--bg-surface)' : 'transparent', color: isRegistering ? 'var(--text-primary)' : 'var(--text-secondary)', boxShadow: isRegistering ? '0 1px 3px rgba(0,0,0,0.1)' : 'none', cursor: 'pointer', fontWeight: 500, transition: 'all 0.2s' }}
              >
                Register
              </button>
            </div>

            <form onSubmit={handleAuth} className="auth-form">
              <input 
                type="email" 
                placeholder="Email address" 
                value={authEmail} 
                onChange={e => setAuthEmail(e.target.value)} 
                required 
              />
              <input 
                type="password" 
                placeholder="Password" 
                value={authPassword} 
                onChange={e => setAuthPassword(e.target.value)} 
                required 
              />
              {authError && <div className="auth-error">{authError}</div>}
              
              <button type="submit" className="btn btn-primary auth-submit" disabled={authLoading} style={{ padding: '0.85rem', marginTop: '0.5rem', borderRadius: 'var(--radius-sm)' }}>
                {authLoading ? (isRegistering ? 'Registering...' : 'Signing in...') : (isRegistering ? 'Create Account' : 'Sign In')}
              </button>
            </form>
            
            <div className="auth-divider" style={{ margin: '1.25rem 0' }}><span>or continue without an account</span></div>
            
            <button onClick={handleGuest} className="btn btn-secondary auth-guest" disabled={authLoading} style={{ width: '100%', padding: '0.85rem', borderRadius: 'var(--radius-sm)' }}>
              Proceed as Guest
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export default App;
