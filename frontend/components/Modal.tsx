"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, X } from "lucide-react";

type BaseProps = {
  open: boolean;
  onClose: () => void;
};

export function Modal({
  open,
  onClose,
  children,
}: BaseProps & { children: React.ReactNode }) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40 backdrop-blur-sm animate-in fade-in duration-150"
      onClick={onClose}
    >
      <div
        className="relative bg-white rounded-2xl shadow-2xl w-full max-w-md overflow-hidden animate-in zoom-in-95 duration-150"
        onClick={(e) => e.stopPropagation()}
      >
        <button
          onClick={onClose}
          className="absolute top-3 right-3 text-gray-400 hover:text-gray-600 transition-colors p-1 rounded-full hover:bg-gray-100"
          aria-label="ปิด"
        >
          <X size={18} />
        </button>
        {children}
      </div>
    </div>
  );
}

export function ConfirmModal({
  open,
  onClose,
  onConfirm,
  title,
  description,
  confirmLabel = "ยืนยัน",
  cancelLabel = "ยกเลิก",
  danger = false,
}: BaseProps & {
  onConfirm: () => void;
  title: string;
  description?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
}) {
  return (
    <Modal open={open} onClose={onClose}>
      <div className="p-6">
        <div className="flex gap-4">
          <div
            className={`flex-shrink-0 w-12 h-12 rounded-2xl flex items-center justify-center ${
              danger
                ? "bg-red-100 text-red-600"
                : "bg-gradient-to-br from-purple-100 to-purple-200 text-purple-700"
            }`}
          >
            {danger ? <AlertTriangle size={24} /> : <CheckCircle2 size={24} />}
          </div>
          <div className="flex-1 pt-1">
            <h2 className="text-lg font-medium text-gray-900">{title}</h2>
            {description && (
              <p className="mt-1.5 text-sm text-gray-600 leading-relaxed">
                {description}
              </p>
            )}
          </div>
        </div>

        <div className="mt-6 flex gap-2 justify-end">
          <button
            onClick={onClose}
            className="px-4 py-2 bg-white border border-gray-200 rounded-xl hover:bg-gray-50 transition-all text-sm text-gray-700"
          >
            {cancelLabel}
          </button>
          <button
            onClick={() => {
              onConfirm();
              onClose();
            }}
            className={`px-5 py-2 text-white rounded-xl transition-all shadow-md text-sm ${
              danger
                ? "bg-gradient-to-r from-red-500 to-red-600 hover:from-red-600 hover:to-red-700"
                : "bg-gradient-to-r from-purple-600 to-purple-700 hover:from-purple-700 hover:to-purple-800"
            }`}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </Modal>
  );
}

export function PromptModal({
  open,
  onClose,
  onConfirm,
  title,
  description,
  initialValue = "",
  placeholder = "",
  confirmLabel = "บันทึก",
  cancelLabel = "ยกเลิก",
}: BaseProps & {
  onConfirm: (value: string) => void;
  title: string;
  description?: string;
  initialValue?: string;
  placeholder?: string;
  confirmLabel?: string;
  cancelLabel?: string;
}) {
  const [value, setValue] = useState(initialValue);

  useEffect(() => {
    if (open) setValue(initialValue);
  }, [open, initialValue]);

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = value.trim();
    if (!trimmed) return;
    onConfirm(trimmed);
    onClose();
  }

  return (
    <Modal open={open} onClose={onClose}>
      <form onSubmit={submit} className="p-6">
        <h2 className="text-lg font-medium text-gray-900 mb-1">{title}</h2>
        {description && <p className="text-sm text-gray-600 mb-4">{description}</p>}

        <input
          type="text"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder={placeholder}
          autoFocus
          className="w-full px-4 py-3 bg-gray-50 border border-gray-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-purple-500 focus:border-transparent transition-all text-sm"
        />

        <div className="mt-6 flex gap-2 justify-end">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 bg-white border border-gray-200 rounded-xl hover:bg-gray-50 transition-all text-sm text-gray-700"
          >
            {cancelLabel}
          </button>
          <button
            type="submit"
            disabled={!value.trim()}
            className="px-5 py-2 bg-gradient-to-r from-purple-600 to-purple-700 text-white rounded-xl hover:from-purple-700 hover:to-purple-800 transition-all shadow-md text-sm disabled:opacity-60"
          >
            {confirmLabel}
          </button>
        </div>
      </form>
    </Modal>
  );
}

export function AlertModal({
  open,
  onClose,
  title,
  description,
  okLabel = "ตกลง",
  variant = "info",
}: BaseProps & {
  title: string;
  description?: string;
  okLabel?: string;
  variant?: "info" | "error" | "success";
}) {
  const palette = {
    info: {
      bg: "bg-gradient-to-br from-purple-100 to-purple-200 text-purple-700",
      icon: <CheckCircle2 size={24} />,
    },
    error: {
      bg: "bg-red-100 text-red-600",
      icon: <AlertTriangle size={24} />,
    },
    success: {
      bg: "bg-green-100 text-green-600",
      icon: <CheckCircle2 size={24} />,
    },
  }[variant];

  return (
    <Modal open={open} onClose={onClose}>
      <div className="p-6">
        <div className="flex gap-4">
          <div
            className={`flex-shrink-0 w-12 h-12 rounded-2xl flex items-center justify-center ${palette.bg}`}
          >
            {palette.icon}
          </div>
          <div className="flex-1 pt-1">
            <h2 className="text-lg font-medium text-gray-900">{title}</h2>
            {description && (
              <p className="mt-1.5 text-sm text-gray-600 leading-relaxed whitespace-pre-wrap">
                {description}
              </p>
            )}
          </div>
        </div>

        <div className="mt-6 flex justify-end">
          <button
            onClick={onClose}
            className="px-5 py-2 bg-gradient-to-r from-purple-600 to-purple-700 text-white rounded-xl hover:from-purple-700 hover:to-purple-800 transition-all shadow-md text-sm"
          >
            {okLabel}
          </button>
        </div>
      </div>
    </Modal>
  );
}
