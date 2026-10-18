import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider } from "./auth";
import App from "./App";
import "./styles.css";

// TanStack Query caches server data and refetches it (on focus and on intervals),
// which is how the UI stays fresh without websockets.
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: true,
      // Don't retry 4xx errors (403/404 won't fix themselves); retry network/5xx once.
      retry: (failureCount, error) => !(error?.status >= 400 && error?.status < 500) && failureCount < 1,
    },
  },
});

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthProvider>
          <App />
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
