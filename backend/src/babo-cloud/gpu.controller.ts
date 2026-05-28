import {
  Controller,
  Get,
  Post,
  Body,
  UseGuards,
  UseInterceptors,
  UploadedFile,
  HttpException,
  HttpStatus,
  Req,
} from '@nestjs/common';
import { FileInterceptor } from '@nestjs/platform-express';
import { CloudAuthGuard } from './cloud-auth.guard';
import { CloudAccessGuard } from './cloud-access.guard';
import { CloudScope } from './cloud-scope.decorator';
import { GpuService } from './gpu.service';
import { CloudUpstreamService } from './cloud-upstream.service';

@Controller('gpu')
@UseGuards(CloudAuthGuard, CloudAccessGuard)
@CloudScope('inference', 'gpu')
export class GpuController {
  constructor(
    private gpu: GpuService,
    private upstream: CloudUpstreamService,
  ) {}

  @Get('health')
  health() {
    return this.gpu.health();
  }

  @Post('transcribe')
  @UseInterceptors(FileInterceptor('audio'))
  async transcribe(
    @Req() req: any,
    @UploadedFile() file: Express.Multer.File,
  ) {
    if (!this.upstream.isGpuConfigured()) {
      throw new HttpException('GPU upstream not configured', HttpStatus.SERVICE_UNAVAILABLE);
    }
    if (!file) {
      throw new HttpException('No audio file', HttpStatus.BAD_REQUEST);
    }
    return this.gpu.proxyTranscribe(req.cloudAuth, file);
  }

  @Post('vision/describe')
  async visionDescribe(
    @Req() req: any,
    @Body() body: Record<string, unknown>,
  ) {
    if (!this.upstream.isGpuConfigured()) {
      throw new HttpException('GPU upstream not configured', HttpStatus.SERVICE_UNAVAILABLE);
    }
    return this.gpu.proxyVisionDescribe(req.cloudAuth, body);
  }

  @Post('embed')
  async embed(@Req() req: any, @Body() body: Record<string, unknown>) {
    if (!this.upstream.isGpuConfigured()) {
      throw new HttpException('GPU upstream not configured', HttpStatus.SERVICE_UNAVAILABLE);
    }
    return this.gpu.proxyEmbed(req.cloudAuth, body);
  }
}
