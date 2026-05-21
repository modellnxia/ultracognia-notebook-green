CREATE TABLE IF NOT EXISTS notebooks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    notebook_id VARCHAR NOT NULL,
    notebook_title VARCHAR NOT NULL,
    target_date DATE NOT NULL,
    report_content TEXT NOT NULL,
    report_path VARCHAR NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, target_date)
);
