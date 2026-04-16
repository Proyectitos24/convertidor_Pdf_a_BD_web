create extension if not exists pgcrypto;

create table if not exists public.stores (
    id uuid primary key default gen_random_uuid(),
    code text not null unique,
    name text not null,
    active boolean not null default true,
    created_at timestamptz not null default now()
);

create table if not exists public.converted_files (
    id uuid primary key default gen_random_uuid(),
    store_id uuid not null references public.stores(id) on delete cascade,
    original_pdf_name text not null,
    db_file_name text not null,
    object_key text not null unique,
    size_bytes bigint not null,
    status text not null default 'ready'
        check (status in ('ready', 'expired', 'deleted', 'error')),
    created_at timestamptz not null default now(),
    expires_at timestamptz not null
);

create index if not exists idx_converted_files_store_created_at
    on public.converted_files (store_id, created_at desc);

create index if not exists idx_converted_files_expires_at
    on public.converted_files (expires_at);

insert into public.stores (code, name)
values
    ('14140', 'Tienda 14140'),
    ('14102', 'Tienda 14102'),
    ('14017', 'Tienda 14017'),
    ('14196', 'Tienda 14196'),
    ('14043', 'Tienda 14043')
on conflict (code) do update
set name = excluded.name,
    active = true;