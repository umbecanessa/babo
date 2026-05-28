import { Body, Controller, Get, Post } from '@nestjs/common';
import { AuthService } from '../auth/auth.service';
import { AdminBootstrapDto } from './dto';
import { AdminService } from './admin.service';

/** Public routes for first-run admin bootstrap (no JWT required). */
@Controller('admin/setup')
export class AdminSetupController {
  constructor(
    private admin: AdminService,
    private auth: AuthService,
  ) {}

  @Get('status')
  getStatus() {
    return this.admin.getSetupStatus();
  }

  @Post()
  async bootstrap(@Body() dto: AdminBootstrapDto) {
    const user = await this.admin.bootstrapFirstAdmin(
      dto.email,
      dto.password,
      dto.displayName,
    );
    return this.auth.login(dto.email, dto.password);
  }
}
