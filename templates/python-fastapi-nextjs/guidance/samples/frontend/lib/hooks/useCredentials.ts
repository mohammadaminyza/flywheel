import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';

export interface Credential {
  id: string;
  name: string;
  provider: string;
  owner_id: string;
  shared_team_ids: string[];
  created_at: string;
  updated_at: string;
}

export interface CreateCredentialInput {
  name: string;
  provider: string;
}

const CREDENTIALS_KEY = ['credentials'] as const;

export function useCredentials() {
  return useQuery({
    queryKey: CREDENTIALS_KEY,
    queryFn: () => api.get<Credential[]>('/credentials'),
  });
}

export function useCreateCredential() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (input: CreateCredentialInput) =>
      api.post<Credential>('/credentials', input),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: CREDENTIALS_KEY });
    },
  });
}

export function useDeleteCredential() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (id: string) => api.delete(`/credentials/${id}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: CREDENTIALS_KEY });
    },
  });
}
