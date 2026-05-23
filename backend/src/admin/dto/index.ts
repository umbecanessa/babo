import { IsString, IsOptional, IsIn } from 'class-validator';

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
