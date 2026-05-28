import { BrowserRouter, Routes, Route, Navigate } from 'react-router';
import LoginPage from './components/LoginPage';
import ChatPage from './components/ChatPage';

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<LoginPage />} />
        <Route path="/chat" element={<ChatPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}