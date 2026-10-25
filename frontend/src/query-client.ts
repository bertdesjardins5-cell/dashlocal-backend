// One QueryClient for the whole app; the provider in app/_layout.tsx uses
// this instance. Import it for cache calls outside components, for example
// queryClient.invalidateQueries or setQueryData in websocket or push
// handlers; inside components useQueryClient() returns this same instance.
import { QueryClient } from "@tanstack/react-query";

// No retries for auth/permission/not-found errors: show them straight away.
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (count, err: any) => !/\((401|403|404)\)|sign in|only|not found|another driver/i.test(String(err?.message ?? "")) && count < 3,
    },
  },
});
