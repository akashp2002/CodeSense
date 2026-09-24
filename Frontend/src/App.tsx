import { useState, useRef, useEffect } from 'react';
import './index.css';

interface Message {
  id: string;
  role: 'user' | 'ai' | 'system';
  content: string;
  diff?: string;
}

function App() {
  const [repoUrl, setRepoUrl] = useState('https://github.com/akashp2002/demo');
  const [query, setQuery] = useState('');
  const [messages, setMessages] = useState<Message[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isCloning, setIsCloning] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  const handleClone = async () => {
    if (!repoUrl) return;
    setIsCloning(true);
    setMessages(prev => [...prev, { id: Date.now().toString(), role: 'system', content: `Cloning repository: ${repoUrl}...` }]);
    
    try {
      const response = await fetch('http://localhost:8001/clone', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ github_url: repoUrl })
      });
      
      const data = await response.json();
      
      if (response.ok) {
        setMessages(prev => [...prev, { id: Date.now().toString(), role: 'system', content: `✅ Successfully cloned to ${data.repo_path}. Ready for analysis.` }]);
      } else {
        setMessages(prev => [...prev, { id: Date.now().toString(), role: 'system', content: `❌ Clone failed: ${data.detail || 'Unknown error'}` }]);
      }
    } catch (error: any) {
      setMessages(prev => [...prev, { id: Date.now().toString(), role: 'system', content: `❌ Network error: ${error.message}` }]);
    } finally {
      setIsCloning(false);
    }
  };

  const handleDeleteRepo = async () => {
    if (!repoUrl) return;
    setIsDeleting(true);
    
    // Extract repo name from URL
    const repoName = repoUrl.split('/').pop()?.replace('.git', '') || '';
    const repoPath = `repos/${repoName}`;
    
    setMessages(prev => [...prev, { id: Date.now().toString(), role: 'system', content: `Removing repository ${repoName}...` }]);
    
    try {
      const response = await fetch('http://localhost:8001/delete-repository', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ repo_path: repoPath })
      });
      
      if (response.ok) {
        setMessages(prev => [...prev, { id: Date.now().toString(), role: 'system', content: `🗑️ Successfully deleted repository ${repoName}.` }]);
      } else {
        const data = await response.json();
        setMessages(prev => [...prev, { id: Date.now().toString(), role: 'system', content: `❌ Delete failed: ${data.detail || 'Unknown error'}` }]);
      }
    } catch (error: any) {
      setMessages(prev => [...prev, { id: Date.now().toString(), role: 'system', content: `❌ Network error: ${error.message}` }]);
    } finally {
      setIsDeleting(false);
    }
  };

  const handleQuery = () => {
    if (!query.trim() || isLoading) return;
    
    const newMsgId = Date.now().toString();
    setMessages(prev => [...prev, { id: newMsgId, role: 'user', content: query }]);
    setQuery('');
    setIsLoading(true);
    
    // Create a temporary system message to show status updates
    const statusMsgId = (Date.now() + 1).toString();
    setMessages(prev => [...prev, { id: statusMsgId, role: 'system', content: 'Connecting to agent...' }]);
    
    // Connect to WebSocket
    const ws = new WebSocket('ws://localhost:8001/ws/query');
    
    ws.onopen = () => {
      ws.send(JSON.stringify({ question: query }));
    };
    
    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      
      if (data.type === 'status' || data.type === 'heartbeat') {
        setMessages(prev => prev.map(msg => 
          msg.id === statusMsgId ? { ...msg, content: data.message } : msg
        ));
      } else if (data.type === 'result') {
        // Remove status message and add final result
        setMessages(prev => prev.filter(msg => msg.id !== statusMsgId));
        setMessages(prev => [...prev, { 
          id: (Date.now() + 2).toString(), 
          role: 'ai', 
          content: data.answer,
          diff: data.diff_data 
        }]);
        ws.close();
        setIsLoading(false);
      } else if (data.type === 'error') {
        setMessages(prev => prev.filter(msg => msg.id !== statusMsgId));
        setMessages(prev => [...prev, { id: (Date.now() + 2).toString(), role: 'system', content: `❌ Error: ${data.message}` }]);
        ws.close();
        setIsLoading(false);
      }
    };
    
    ws.onerror = () => {
      setMessages(prev => prev.filter(msg => msg.id !== statusMsgId));
      setMessages(prev => [...prev, { id: (Date.now() + 2).toString(), role: 'system', content: `❌ WebSocket connection failed.` }]);
      setIsLoading(false);
    };
  };

  const handleApprovePR = async (prompt: string) => {
    setMessages(prev => [...prev, { id: Date.now().toString(), role: 'system', content: `Pushing changes and creating PR...` }]);
    
    try {
      // In a real app, you'd extract the repo name properly
      const repoName = repoUrl.split('/').pop() || 'demo';
      
      const response = await fetch('http://localhost:8001/create-pr', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt, repo_name: repoName })
      });
      
      const data = await response.json();
      
      if (response.ok) {
        setMessages(prev => [...prev, { id: Date.now().toString(), role: 'system', content: `✅ ${data.message}` }]);
      } else {
        setMessages(prev => [...prev, { id: Date.now().toString(), role: 'system', content: `❌ PR Creation failed: ${data.detail || 'Unknown error'}` }]);
      }
    } catch (error: any) {
      setMessages(prev => [...prev, { id: Date.now().toString(), role: 'system', content: `❌ Network error: ${error.message}` }]);
    }
  };

  return (
    <div className="app-container">
      <header>
        <h1>CodeSense</h1>
        <p style={{ color: 'var(--text-secondary)' }}>AI Agent for Codebase QA & Impact Analysis</p>
      </header>

      <section className="panel">
        <h3 style={{ marginBottom: '1rem', color: 'var(--text-secondary)', fontSize: '0.875rem', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
          Repository Configuration
        </h3>
        <div className="clone-section">
          <input 
            type="text" 
            value={repoUrl} 
            onChange={(e) => setRepoUrl(e.target.value)}
            placeholder="Enter GitHub Repository URL"
            disabled={isCloning}
          />
          <button className="primary" onClick={handleClone} disabled={isCloning || isDeleting}>
            {isCloning ? 'Cloning...' : 'Clone & Index'}
          </button>
          <button className="danger" onClick={handleDeleteRepo} disabled={isCloning || isDeleting || !repoUrl}>
            {isDeleting ? 'Removing...' : 'Delete Repo'}
          </button>
        </div>
      </section>

      <section className="panel chat-container">
        <div className="messages">
          {messages.length === 0 ? (
            <div style={{ textAlign: 'center', color: 'var(--text-secondary)', marginTop: '2rem' }}>
              No messages yet. Try cloning a repository and asking a question!
            </div>
          ) : (
            messages.map((msg) => (
              <div key={msg.id} className={`message ${msg.role} ${msg.role === 'system' && isLoading && msg === messages[messages.length-1] ? 'pulse' : ''}`}>
                <div>{msg.content}</div>
                {msg.diff && (
                  <div className="diff-container">
                    <div className="diff-block">{msg.diff}</div>
                    <div className="diff-actions">
                      <button className="approve" onClick={() => handleApprovePR("Apply approved refactor")}>Approve & Create PR</button>
                      <button className="reject" onClick={() => setMessages(prev => [...prev, { id: Date.now().toString(), role: 'system', content: '❌ Refactor rejected.' }])}>Reject</button>
                    </div>
                  </div>
                )}
              </div>
            ))
          )}
          <div ref={messagesEndRef} />
        </div>
        
        <div className="clone-section" style={{ marginTop: 'auto' }}>
          <input 
            type="text" 
            value={query} 
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleQuery()}
            placeholder="Ask about the codebase or request a refactor..."
            disabled={isLoading}
          />
          <button className="primary" onClick={handleQuery} disabled={isLoading || !query.trim()}>
            {isLoading ? 'Processing...' : 'Send'}
          </button>
        </div>
      </section>
    </div>
  );
}

export default App;
