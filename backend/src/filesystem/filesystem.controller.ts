import {
  Controller,
  Get,
  Post,
  Body,
  Query,
  UseGuards,
} from '@nestjs/common';
import { JwtAuthGuard } from '../auth/jwt-auth.guard';
import { FilesystemService } from './filesystem.service';

@Controller('fs')
@UseGuards(JwtAuthGuard)
export class FilesystemController {
  constructor(private fs: FilesystemService) {}

  @Get('tree')
  getTree(
    @Query('path') path: string,
    @Query('depth') depth?: string,
    @Query('glob') glob?: string,
  ) {
    return this.fs.getTree(path, depth ? parseInt(depth, 10) : 3, glob || '');
  }

  @Get('read')
  readFile(
    @Query('path') path: string,
    @Query('offset') offset?: string,
    @Query('limit') limit?: string,
  ) {
    return this.fs.readFile(
      path,
      offset ? parseInt(offset, 10) : 0,
      limit ? parseInt(limit, 10) : 0,
    );
  }

  @Post('write')
  writeFile(@Body() body: { path: string; content: string; append?: boolean }) {
    return this.fs.writeFile(body.path, body.content, body.append || false);
  }

  @Post('edit')
  editFile(
    @Body()
    body: {
      path: string;
      old_string: string;
      new_string: string;
      replace_all?: boolean;
    },
  ) {
    return this.fs.editFile(
      body.path,
      body.old_string,
      body.new_string,
      body.replace_all || false,
    );
  }

  @Get('search')
  searchFiles(
    @Query('pattern') pattern: string,
    @Query('path') path: string,
    @Query('glob') glob?: string,
    @Query('max_results') maxResults?: string,
  ) {
    return this.fs.searchFiles(
      pattern,
      path,
      glob || '',
      maxResults ? parseInt(maxResults, 10) : 50,
    );
  }

  @Get('readdir')
  readDir(@Query('path') path: string) {
    return this.fs.readDir(path);
  }
}
