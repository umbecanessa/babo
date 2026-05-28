import { IsString, IsOptional, IsInt, Min, Max, IsUUID, IsArray } from 'class-validator';

export class CreateApiKeyDto {
  @IsString()
  name: string;

  @IsInt()
  @IsOptional()
  @Min(1)
  @Max(1000)
  rateLimitRpm?: number;

  @IsUUID()
  @IsOptional()
  agentId?: string;

  @IsArray()
  @IsString({ each: true })
  @IsOptional()
  scopes?: string[];
}
