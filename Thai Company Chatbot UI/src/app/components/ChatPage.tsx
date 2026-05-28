import { useState } from 'react';
import { Plus, Search, MessageSquare, LogOut, Send } from 'lucide-react';
import { useNavigate } from 'react-router';

type Message = {
  id: number;
  text: string;
  sender: 'user' | 'bot';
};

type ChatHistory = {
  id: number;
  title: string;
  active?: boolean;
};

export default function ChatPage() {
  const navigate = useNavigate();
  const [inputMessage, setInputMessage] = useState('');
  const [messages, setMessages] = useState<Message[]>([
    {
      id: 1,
      text: 'บริษัทเปิดมาแล้วกี่ปีครับ',
      sender: 'user',
    },
    {
      id: 2,
      text: 'บริษัท ศิริวัฒนาอินเตอร์พริ้นท์ จำกัด (มหาชน) มีประสบการณ์ในธุรกิจสิ่งพิมพ์มากกว่า 45 ปี ซึ่งแสดงให้เห็นถึงความเชี่ยวชาญและประสบการณ์ในอุตสาหกรรมสิ่งพิมพ์และบรรจุภัณฑ์ค่ะ',
      sender: 'bot',
    },
    {
      id: 3,
      text: 'มีพนักงานกี่คนครับ',
      sender: 'user',
    },
    {
      id: 4,
      text: 'บริษัท ศิริวัฒนาอินเตอร์พริ้นท์ จำกัด (มหาชน) มีพนักงานมากกว่า 3,000 คนค่ะ หากต้องการข้อมูลเพิ่มเติมเกี่ยวกับบริษัท สามารถติดต่อได้ที่ +66(0)89-969-2859 หรืออีเมล sirivatanaonline@gmail.com ค่ะ',
      sender: 'bot',
    },
  ]);

  const [chatHistory] = useState<ChatHistory[]>([
    { id: 1, title: 'บริษัทเปิดมาแล้วกี่ปีครับ', active: true },
    { id: 2, title: 'มีพนักงานกี่คนครับ' },
    { id: 3, title: 'รับผลิตกล่องอะไรบ้าง' },
  ]);

  const handleSendMessage = (e: React.FormEvent) => {
    e.preventDefault();
    if (!inputMessage.trim()) return;

    const newMessage: Message = {
      id: messages.length + 1,
      text: inputMessage,
      sender: 'user',
    };

    setMessages([...messages, newMessage]);
    setInputMessage('');

    setTimeout(() => {
      const botResponse: Message = {
        id: messages.length + 2,
        text: 'ขอบคุณสำหรับคำถามค่ะ ระบบกำลังประมวลผลข้อมูลเพื่อตอบคำถามของคุณ',
        sender: 'bot',
      };
      setMessages((prev) => [...prev, botResponse]);
    }, 1000);
  };

  const handleLogout = () => {
    navigate('/');
  };

  return (
    <div className="flex h-screen w-full bg-gray-50">
      {/* Left Sidebar */}
      <aside className="w-80 bg-gradient-to-b from-purple-700 via-purple-800 to-purple-900 flex flex-col shadow-2xl">
        {/* New Chat Button */}
        <div className="p-4">
          <button className="w-full flex items-center justify-center gap-2 bg-white/10 hover:bg-white/20 text-white py-3 px-4 rounded-xl transition-all backdrop-blur-sm border border-white/20 shadow-lg">
            <Plus size={20} />
            <span>แชทใหม่</span>
          </button>
        </div>

        {/* Search */}
        <div className="px-4 pb-4">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-purple-200" size={18} />
            <input
              type="text"
              placeholder="ค้นหาในประวัติ…"
              className="w-full pl-10 pr-4 py-2.5 bg-white/10 border border-white/20 rounded-lg text-white placeholder-purple-200 focus:outline-none focus:ring-2 focus:ring-white/30 transition-all backdrop-blur-sm"
            />
          </div>
        </div>

        {/* Chat History */}
        <div className="flex-1 overflow-y-auto px-4">
          <div className="mb-3 text-purple-200 uppercase tracking-wide">
            วันนี้
          </div>
          <div className="space-y-1">
            {chatHistory.map((chat) => (
              <button
                key={chat.id}
                className={`w-full flex items-center gap-3 px-4 py-3 rounded-lg text-left transition-all ${
                  chat.active
                    ? 'bg-purple-600 text-white shadow-lg'
                    : 'text-purple-100 hover:bg-white/10'
                }`}
              >
                <MessageSquare size={18} className="flex-shrink-0" />
                <span className="truncate">{chat.title}</span>
              </button>
            ))}
          </div>
        </div>

        {/* Logout Button */}
        <div className="p-4 border-t border-white/20">
          <button
            onClick={handleLogout}
            className="w-full flex items-center justify-center gap-2 text-purple-100 hover:text-white hover:bg-white/10 py-3 px-4 rounded-lg transition-all"
          >
            <LogOut size={20} />
            <span>ออกจากระบบ</span>
          </button>
        </div>
      </aside>

      {/* Main Chat Area */}
      <main className="flex-1 flex flex-col">
        {/* Header */}
        <header className="bg-white border-b border-gray-200 px-8 py-4 shadow-sm">
          <h2 className="text-gray-800">
            บริษัทเปิดมาแล้วกี่ปีครับ
          </h2>
        </header>

        {/* Messages Area */}
        <div className="flex-1 overflow-y-auto bg-gradient-to-b from-purple-50/30 to-white">
          <div className="max-w-4xl mx-auto px-8 py-8 space-y-6">
            {messages.map((message) => (
              <div
                key={message.id}
                className={`flex ${message.sender === 'user' ? 'justify-end' : 'justify-start'}`}
              >
                <div
                  className={`max-w-2xl px-6 py-4 rounded-2xl shadow-md ${
                    message.sender === 'user'
                      ? 'bg-gradient-to-r from-purple-600 to-purple-700 text-white ml-auto'
                      : 'bg-white text-gray-800 border border-gray-100'
                  }`}
                >
                  <p className="leading-relaxed">{message.text}</p>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Input Area */}
        <div className="bg-white border-t border-gray-200 px-8 py-6 shadow-lg">
          <form onSubmit={handleSendMessage} className="max-w-4xl mx-auto">
            <div className="flex gap-3">
              <input
                type="text"
                value={inputMessage}
                onChange={(e) => setInputMessage(e.target.value)}
                placeholder="พิมพ์คำถาม…"
                className="flex-1 px-6 py-4 bg-gray-50 border border-gray-200 rounded-2xl focus:outline-none focus:ring-2 focus:ring-purple-500 focus:border-transparent transition-all"
              />
              <button
                type="submit"
                className="px-8 py-4 bg-gradient-to-r from-purple-600 to-purple-700 text-white rounded-2xl hover:from-purple-700 hover:to-purple-800 transition-all shadow-md hover:shadow-lg transform hover:-translate-y-0.5 flex items-center gap-2"
              >
                <Send size={20} />
                <span>ส่ง</span>
              </button>
            </div>
          </form>
        </div>
      </main>
    </div>
  );
}
