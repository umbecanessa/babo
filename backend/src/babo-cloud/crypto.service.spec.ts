import { ConfigService } from '@nestjs/config';
import { CryptoService } from './crypto.service';

describe('CryptoService', () => {
  const config = {
    get: (key: string) =>
      key === 'SECRETS_ENCRYPTION_KEY' ? 'test-secret-key' : undefined,
  } as ConfigService;

  it('round-trips plaintext', () => {
    const crypto = new CryptoService(config);
    const enc = crypto.encrypt('sk-test-123');
    expect(crypto.decrypt(enc)).toBe('sk-test-123');
  });
});
