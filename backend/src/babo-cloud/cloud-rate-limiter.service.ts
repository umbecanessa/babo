import { HttpException, HttpStatus, Injectable } from '@nestjs/common';
import { CloudAuthContext } from './cloud-auth.types';

interface Bucket {
  count: number;
  windowStart: number;
}

@Injectable()
export class CloudRateLimiterService {
  private buckets = new Map<string, Bucket>();

  assertWithinLimit(auth: CloudAuthContext, rpm: number): void {
    const key = auth.apiKeyId ?? `user:${auth.userId}`;
    const now = Date.now();
    const windowMs = 60_000;
    let bucket = this.buckets.get(key);

    if (!bucket || now - bucket.windowStart >= windowMs) {
      bucket = { count: 0, windowStart: now };
      this.buckets.set(key, bucket);
    }

    bucket.count += 1;
    if (bucket.count > rpm) {
      throw new HttpException(
        'Rate limit exceeded',
        HttpStatus.TOO_MANY_REQUESTS,
      );
    }
  }
}
