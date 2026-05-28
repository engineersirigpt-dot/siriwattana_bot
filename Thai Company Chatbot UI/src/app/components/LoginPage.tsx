import { useState } from 'react';
import { useNavigate } from 'react-router';
import { Lock, User } from 'lucide-react';

export default function LoginPage() {
  const navigate = useNavigate();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');

  const handleLogin = (e: React.FormEvent) => {
    e.preventDefault();
    navigate('/chat');
  };

  return (
    <div className="min-h-screen w-full flex items-center justify-center bg-gradient-to-br from-white via-purple-50 to-purple-100 relative overflow-hidden">
      {/* Background decoration */}
      <div className="absolute inset-0 overflow-hidden pointer-events-none">
        <div className="absolute -top-40 -right-40 w-96 h-96 bg-purple-300 rounded-full opacity-20 blur-3xl"></div>
        <div className="absolute -bottom-40 -left-40 w-96 h-96 bg-pink-300 rounded-full opacity-15 blur-3xl"></div>
      </div>

      {/* Login Card */}
      <div className="relative z-10 bg-white rounded-3xl shadow-2xl p-12 w-full max-w-md">
        {/* Logo placeholder */}
        <div className="flex justify-center mb-8">
          <div className="w-20 h-20 bg-gradient-to-br from-purple-600 to-purple-800 rounded-2xl flex items-center justify-center shadow-lg">
            <div className="w-12 h-12 border-4 border-white rounded-xl"></div>
          </div>
        </div>

        {/* Title */}
        <div className="text-center mb-8">
          <h1 className="mb-2 bg-gradient-to-r from-purple-600 to-purple-800 bg-clip-text text-transparent">
            ระบบแชทบอทบริษัท
          </h1>
          <p className="text-gray-600">
            เข้าสู่ระบบเพื่อสอบถามข้อมูลบริษัท
          </p>
        </div>

        {/* Login Form */}
        <form onSubmit={handleLogin} className="space-y-5">
          {/* Email Input */}
          <div>
            <div className="relative">
              <div className="absolute left-4 top-1/2 -translate-y-1/2 text-gray-400">
                <User size={20} />
              </div>
              <input
                type="text"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="อีเมลหรือชื่อผู้ใช้"
                className="w-full pl-12 pr-4 py-3.5 bg-gray-50 border border-gray-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-purple-500 focus:border-transparent transition-all"
              />
            </div>
          </div>

          {/* Password Input */}
          <div>
            <div className="relative">
              <div className="absolute left-4 top-1/2 -translate-y-1/2 text-gray-400">
                <Lock size={20} />
              </div>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="รหัสผ่าน"
                className="w-full pl-12 pr-4 py-3.5 bg-gray-50 border border-gray-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-purple-500 focus:border-transparent transition-all"
              />
            </div>
          </div>

          {/* Forgot Password Link */}
          <div className="text-right">
            <a href="#" className="text-purple-600 hover:text-purple-700 transition-colors">
              ลืมรหัสผ่าน?
            </a>
          </div>

          {/* Login Button */}
          <button
            type="submit"
            className="w-full py-3.5 bg-gradient-to-r from-purple-600 to-purple-800 text-white rounded-xl hover:from-purple-700 hover:to-purple-900 transition-all shadow-lg hover:shadow-xl transform hover:-translate-y-0.5"
          >
            เข้าสู่ระบบ
          </button>
        </form>

        {/* Footer */}
        <div className="mt-8 text-center text-gray-500">
          บริษัท ศิริวัฒนาอินเตอร์พริ้นท์ จำกัด (มหาชน)
        </div>
      </div>
    </div>
  );
}
