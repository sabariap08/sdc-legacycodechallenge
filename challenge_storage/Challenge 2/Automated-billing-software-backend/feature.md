# New Feature Request: Receipt Timestamp

**Difficulty:** Beginner (Perfect for a 1st-year UG student!)

## Description
Currently, when a customer finishes their billing process and confirms their purchase, the system prints a simple success message:
`"Purchase successful! Thank you for shopping!"`

In a real-world scenario, a receipt should always have the date and time of the transaction. We would like you to add a feature that prints the current timestamp when a successful purchase is made.

## Requirements
1. The timestamp should be printed immediately after the "Purchase successful!" message in the `customer()` function.
2. The format of the time should look like this: `YYYY-MM-DD HH:MM:SS` (e.g., `2024-05-15 14:30:00`).