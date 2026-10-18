// Who is logged in. The token lives in localStorage; the user (with team roles) comes from GET /api/me.
import { createContext, useContext } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, getToken, setToken } from "./api";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const queryClient = useQueryClient();
  const me = useQuery({
    queryKey: ["me"],
    queryFn: () => api("/me"),
    enabled: Boolean(getToken()),
    staleTime: 60_000,
  });

  async function login(username, password) {
    const { token, user } = await api("/auth/login", { method: "POST", body: { username, password } });
    setToken(token);
    queryClient.setQueryData(["me"], user);
  }

  async function logout() {
    await api("/auth/logout", { method: "POST" }).catch(() => {});
    setToken(null);
    queryClient.clear();
    window.location.assign("/");
  }

  const value = {
    user: getToken() ? me.data : null,
    loading: Boolean(getToken()) && me.isLoading,
    login,
    logout,
  };
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export const useAuth = () => useContext(AuthContext);
