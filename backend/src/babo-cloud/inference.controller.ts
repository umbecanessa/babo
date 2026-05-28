import {
  Controller,
  Get,
  Post,
  Body,
  Req,
  Res,
  UseGuards,
  HttpException,
  HttpStatus,
} from '@nestjs/common';
import type { Response } from 'express';
import { CloudAuthGuard } from './cloud-auth.guard';
import { CloudAccessGuard } from './cloud-access.guard';
import { CloudScope } from './cloud-scope.decorator';
import { InferenceService } from './inference.service';
import { CloudUpstreamService } from './cloud-upstream.service';

@Controller('inference/v1')
@UseGuards(CloudAuthGuard, CloudAccessGuard)
@CloudScope('inference')
export class InferenceController {
  constructor(
    private inference: InferenceService,
    private upstream: CloudUpstreamService,
  ) {}

  @Get('models')
  async listModels(@Req() req: any) {
    if (!this.upstream.isInferenceConfigured()) {
      throw new HttpException(
        'Inference upstream not configured',
        HttpStatus.SERVICE_UNAVAILABLE,
      );
    }
    return this.inference.listModels(req.cloudAuth);
  }

  @Post('chat/completions')
  async chatCompletions(
    @Req() req: any,
    @Res() res: Response,
    @Body() body: Record<string, unknown>,
  ) {
    if (!this.upstream.isInferenceConfigured()) {
      throw new HttpException(
        'Inference upstream not configured',
        HttpStatus.SERVICE_UNAVAILABLE,
      );
    }
    await this.inference.proxyChatCompletions(req.cloudAuth, body, res);
  }
}
