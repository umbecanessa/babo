import {
  Controller,
  Post,
  UseGuards,
  UseInterceptors,
  UploadedFile,
  HttpException,
  HttpStatus,
} from '@nestjs/common';
import { FileInterceptor } from '@nestjs/platform-express';
import { JwtAuthGuard } from '../auth/jwt-auth.guard';
import { RuntimeService } from '../runtime/runtime.service';

@Controller('transcribe')
@UseGuards(JwtAuthGuard)
export class TranscribeController {
  constructor(private runtime: RuntimeService) {}

  @Post()
  @UseInterceptors(FileInterceptor('audio'))
  async transcribe(@UploadedFile() file: Express.Multer.File) {
    if (!file) {
      throw new HttpException('No audio file provided', HttpStatus.BAD_REQUEST);
    }

    try {
      return await this.runtime.transcribeAudio(file.buffer, file.originalname);
    } catch (err: any) {
      throw new HttpException(
        err.message || 'Transcription failed',
        HttpStatus.INTERNAL_SERVER_ERROR,
      );
    }
  }
}
