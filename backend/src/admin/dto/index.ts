import { IsString, IsOptional, IsIn, IsEmail, MinLength } from 'class-validator';

export class AdminBootstrapDto {
  @IsEmail()
  email!: string;

  @IsString()
  @MinLength(8)
  password!: string;

  @IsOptional()
  @IsString()
  displayName?: string;
}

export class UpdateRoleDto {
  @IsString()
  @IsIn(['user', 'admin'])
  role!: string;
}

export class AgentEventsQueryDto {
  @IsOptional()
  @IsString()
  event_type?: string;

  @IsOptional()
  @IsString()
  from?: string;

  @IsOptional()
  @IsString()
  to?: string;

  @IsOptional()
  @IsString()
  limit?: string;
}

export class AgentFactsQueryDto {
  @IsOptional()
  @IsString()
  search?: string;

  @IsOptional()
  @IsString()
  domain?: string;

  @IsOptional()
  @IsString()
  page?: string;

  @IsOptional()
  @IsString()
  limit?: string;

  @IsOptional()
  @IsString()
  fluid?: string;
}
