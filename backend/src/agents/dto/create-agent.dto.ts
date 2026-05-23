import { IsString, IsOptional } from 'class-validator';

export class CreateAgentDto {
  @IsString()
  @IsOptional()
  genesisVersion?: string;

  @IsString()
  @IsOptional()
  name?: string;

  @IsString()
  @IsOptional()
  sovereignty?: string;
}
