'use client';

import { useState } from 'react';
import { useCredentials, useCreateCredential } from '@/lib/hooks/useCredentials';
import { useI18n } from '@/lib/i18n';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { CredentialCard } from '@/components/features/credentials/CredentialCard';

export default function CredentialsPage() {
  const { t } = useI18n();
  const [name, setName] = useState('');
  const { data: credentials, isLoading, error } = useCredentials();
  const createCredential = useCreateCredential();

  if (isLoading) return <p className="p-6">{t('common.loading')}</p>;
  if (error) return <p className="p-6 text-destructive">{t('credentials.loadFailed')}</p>;

  return (
    <main className="p-6 space-y-6">
      <header className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">{t('credentials.title')}</h1>
        <span className="text-muted-foreground">
          {t('credentials.count', { count: credentials?.length ?? 0 })}
        </span>
      </header>

      <form
        className="flex gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          if (!name.trim()) return;
          createCredential.mutate({ name, provider: 'docker' });
          setName('');
        }}
      >
        <Input
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder={t('credentials.namePlaceholder')}
          aria-label={t('credentials.namePlaceholder')}
        />
        <Button type="submit" disabled={createCredential.isPending}>
          {createCredential.isPending ? t('common.saving') : t('credentials.add')}
        </Button>
      </form>

      {credentials?.length === 0 ? (
        <p className="text-muted-foreground">{t('credentials.empty')}</p>
      ) : (
        <ul className="grid gap-3 md:grid-cols-2">
          {credentials?.map((credential) => (
            <CredentialCard key={credential.id} credential={credential} />
          ))}
        </ul>
      )}
    </main>
  );
}
