import { Injectable, UnauthorizedException, ConflictException } from '@nestjs/common';
import { JwtService } from '@nestjs/jwt';
import { ConfigService } from '@nestjs/config';
import * as bcrypt from 'bcrypt';
import { PrismaService } from '../prisma/prisma.service';
import { EntitlementsService } from '../babo-cloud/entitlements.service';

@Injectable()
export class AuthService {
  constructor(
    private prisma: PrismaService,
    private jwt: JwtService,
    private config: ConfigService,
    private entitlements: EntitlementsService,
  ) {}

  async register(email: string, password: string, displayName?: string) {
    const existing = await this.prisma.user.findUnique({ where: { email } });
    if (existing) {
      throw new ConflictException('Email already registered');
    }

    const passwordHash = await bcrypt.hash(password, 12);

    const user = await this.prisma.user.create({
      data: { email, passwordHash, displayName },
    });

    await this.entitlements.ensureSubscriptionForUser(user.id);

    return this.generateTokens(user.id, user.email, user.role, displayName);
  }

  async login(email: string, password: string) {
    const user = await this.prisma.user.findUnique({ where: { email } });
    if (!user) {
      throw new UnauthorizedException('Invalid credentials');
    }

    const valid = await bcrypt.compare(password, user.passwordHash);
    if (!valid) {
      throw new UnauthorizedException('Invalid credentials');
    }

    await this.entitlements.ensureSubscriptionForUser(user.id);

    return this.generateTokens(user.id, user.email, user.role, user.displayName ?? undefined);
  }

  /** Operator console only — rejects non-admin accounts before issuing tokens. */
  async loginAdmin(email: string, password: string) {
    const user = await this.prisma.user.findUnique({ where: { email } });
    if (!user) {
      throw new UnauthorizedException('Invalid credentials');
    }

    const valid = await bcrypt.compare(password, user.passwordHash);
    if (!valid) {
      throw new UnauthorizedException('Invalid credentials');
    }

    if (user.role !== 'admin') {
      throw new UnauthorizedException(
        'Administrator access only. Use the main Babo app for user accounts.',
      );
    }

    return this.generateTokens(user.id, user.email, user.role, user.displayName ?? undefined);
  }

  async refresh(refreshToken: string) {
    try {
      const payload = this.jwt.verify(refreshToken, {
        secret: this.config.get('JWT_REFRESH_SECRET'),
      });
      // Re-fetch user to get current role (may have been promoted/demoted)
      const user = await this.prisma.user.findUnique({ where: { id: payload.sub } });
      if (!user) throw new UnauthorizedException('User not found');
      return this.generateTokens(user.id, user.email, user.role, user.displayName ?? undefined);
    } catch {
      throw new UnauthorizedException('Invalid refresh token');
    }
  }

  private generateTokens(userId: string, email: string, role: string = 'user', displayName?: string) {
    const payload: Record<string, string> = { sub: userId, email, role };
    if (displayName) payload['name'] = displayName;

    const accessToken = this.jwt.sign(payload, {
      secret: this.config.get('JWT_SECRET'),
      expiresIn: this.config.get('JWT_EXPIRATION') || '15m',
    });

    const refreshToken = this.jwt.sign(payload, {
      secret: this.config.get('JWT_REFRESH_SECRET'),
      expiresIn: this.config.get('JWT_REFRESH_EXPIRATION') || '7d',
    });

    return { accessToken, refreshToken, userId, role };
  }
}
