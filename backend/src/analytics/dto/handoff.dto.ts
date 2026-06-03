import { IsObject, IsOptional, IsString, MaxLength } from 'class-validator';

export class CreateHandoffDto {
  @IsOptional()
  @IsString()
  @MaxLength(64)
  visitorId?: string;

  @IsOptional()
  @IsObject()
  properties?: Record<string, unknown>;
}

export class ClaimHandoffDto {
  @IsString()
  @MaxLength(64)
  installId: string;
}
