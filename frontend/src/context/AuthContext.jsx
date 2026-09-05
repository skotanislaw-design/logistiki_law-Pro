import { createContext, useContext, useEffect, useState } from "react";
import axios, { API } from "@/lib/api";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);

  const checkAuth = async () => {
    try {
      const { data } = await axios.get(`${API}/auth/me`);
      setUser(data);
    } catch {
      setUser(false);
    }
  };

  useEffect(() => {
    checkAuth();
  }, []);

  const login = async (email, password) => {
    const { data } = await axios.post(`${API}/auth/login`, { email, password });
    // Με ενεργό 2FA δεν υπάρχει ακόμη συνεδρία: ο caller ζητά τον κωδικό μιας χρήσης.
    if (data?.mfa_required) return data;
    setUser(data);
    return data;
  };

  const verifyMfa = async (mfaToken, code) => {
    const { data } = await axios.post(`${API}/auth/2fa/verify`, { mfa_token: mfaToken, code });
    setUser(data);
    return data;
  };

  const logout = async () => {
    try {
      await axios.post(`${API}/auth/logout`);
    } finally {
      setUser(false);
    }
  };

  return (
    <AuthContext.Provider value={{ user, setUser, login, verifyMfa, logout, checkAuth }}>
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);
