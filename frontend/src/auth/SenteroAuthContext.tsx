import { ReactNode, createContext, useContext, useEffect, useMemo, useState } from 'react';
import { api, type SenteroUser } from '@shared/api/client';

type LoginInput = {
  email: string;
  password: string;
  remember: boolean;
};

type SetupInput = {
  name: string;
  email: string;
  password: string;
  passwordConfirm: string;
};

type SenteroAuthContextValue = {
  loading: boolean;
  setupRequired: boolean;
  isAuthenticated: boolean;
  user: SenteroUser | null;
  refresh: () => Promise<void>;
  setup: (input: SetupInput) => Promise<boolean>;
  login: (input: LoginInput) => Promise<boolean>;
  logout: () => Promise<void>;
  updateMe: (input: { displayName: string; email: string }) => Promise<SenteroUser>;
  changePassword: (input: { currentPassword: string; newPassword: string; newPasswordConfirm: string }) => Promise<boolean>;
  forgotPassword: (email: string) => Promise<string>;
  resetPassword: (token: string, password: string, passwordConfirm: string) => Promise<boolean>;
};

const SenteroAuthContext = createContext<SenteroAuthContextValue | null>(null);

export function SenteroAuthProvider({ children }: { children: ReactNode }) {
  const [loading, setLoading] = useState(true);
  const [setupRequired, setSetupRequired] = useState(false);
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [user, setUser] = useState<SenteroUser | null>(null);

  async function refresh() {
    const status = await api.senteroAuthStatus();
    setSetupRequired(status.setup_required);
    setIsAuthenticated(status.authenticated);
    setUser(status.user || null);
  }

  useEffect(() => {
    refresh().catch(() => {
      setSetupRequired(false);
      setIsAuthenticated(false);
      setUser(null);
    }).finally(() => setLoading(false));
  }, []);

  const value = useMemo<SenteroAuthContextValue>(() => ({
    loading,
    setupRequired,
    isAuthenticated,
    user,
    refresh,
    setup: async ({ name, email, password, passwordConfirm }) => {
      const response = await api.senteroSetup({ name, email, password, password_confirm: passwordConfirm });
      setSetupRequired(false);
      setIsAuthenticated(response.authenticated);
      setUser(response.user);
      return response.authenticated;
    },
    login: async ({ email, password }) => {
      if (!email.trim() || !password.trim()) return false;
      const response = await api.senteroLogin(email, password);
      setIsAuthenticated(response.authenticated);
      setUser(response.user);
      return response.authenticated;
    },
    logout: async () => {
      await api.senteroLogout().catch(() => undefined);
      setIsAuthenticated(false);
      setUser(null);
    },
    updateMe: async ({ displayName, email }) => {
      const response = await api.updateSenteroMe({ display_name: displayName, email });
      setUser(response.user);
      return response.user;
    },
    changePassword: async ({ currentPassword, newPassword, newPasswordConfirm }) => {
      const response = await api.changeSenteroPassword({
        current_password: currentPassword,
        new_password: newPassword,
        new_password_confirm: newPasswordConfirm,
      });
      return response.ok;
    },
    forgotPassword: async (email) => {
      const response = await api.senteroForgotPassword(email);
      return response.message;
    },
    resetPassword: async (token, password, passwordConfirm) => {
      const response = await api.senteroResetPassword({ token, password, password_confirm: passwordConfirm });
      return response.ok;
    },
  }), [loading, setupRequired, isAuthenticated, user]);

  return <SenteroAuthContext.Provider value={value}>{children}</SenteroAuthContext.Provider>;
}

export function useSenteroAuth() {
  const context = useContext(SenteroAuthContext);
  if (!context) {
    throw new Error('useSenteroAuth must be used inside SenteroAuthProvider');
  }
  return context;
}
