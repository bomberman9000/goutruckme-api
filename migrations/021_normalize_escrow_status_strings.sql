-- Normalize escrow-related statuses to lowercase strings so ORM and raw SQL
-- both agree on the stored representation.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'cargos'
          AND column_name = 'payment_status'
    ) THEN
        ALTER TABLE cargos
            ALTER COLUMN payment_status TYPE VARCHAR(32)
            USING COALESCE(LOWER(payment_status::text), 'unsecured');

        ALTER TABLE cargos
            ALTER COLUMN payment_status SET DEFAULT 'unsecured';

        UPDATE cargos
        SET payment_status = 'unsecured'
        WHERE payment_status IS NULL;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'escrow_deals'
          AND column_name = 'status'
    ) THEN
        ALTER TABLE escrow_deals
            ALTER COLUMN status TYPE VARCHAR(32)
            USING COALESCE(LOWER(status::text), 'payment_pending');

        ALTER TABLE escrow_deals
            ALTER COLUMN status SET DEFAULT 'payment_pending';

        UPDATE escrow_deals
        SET status = 'payment_pending'
        WHERE status IS NULL;
    END IF;
END $$;
