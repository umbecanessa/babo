import { IsString, IsOptional, IsInt, Min, Max } from 'class-validator';

export class CreateApiKeyDto {
  @IsString()
  name: string;

  @IsInt()
  @IsOptional()
  @Min(1)
  @Max(1000)
  rateLimitRpm?: number;
}
