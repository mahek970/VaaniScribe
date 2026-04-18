from __future__ import annotations

from snowflake_utils import save_meeting


fake_meetings = [
    {
        "title": "Q3 Budget Review",
        "transcript": "Toh aaj hum Q3 ka budget discuss kar rahe hain. Rahul ne bataya ki marketing ka budget 20 percent badhana padega. We decided to allocate extra funds from the contingency reserve. Priya will prepare the revised budget sheet by Friday.",
        "summary": {
            "summary": "Q3 budget review meeting. Marketing budget increase approved.",
            "decisions": [
                "Increase marketing budget by 20 percent",
                "Use contingency reserve for additional funds",
            ],
            "action_items": [
                {
                    "task": "Prepare revised budget sheet",
                    "owner": "Priya",
                    "deadline": "Friday",
                }
            ],
            "key_points": [
                "20 percent marketing budget increase",
                "Contingency reserve to be used",
            ],
        },
    },
    {
        "title": "Product Roadmap Discussion",
        "transcript": "Is meeting mein humne next quarter ke features discuss kiye. Voice search feature ko priority deni hai. The team agreed that we need at least 3 engineers on this. Amit will lead the voice search module.",
        "summary": {
            "summary": "Product roadmap planning for next quarter focusing on voice search.",
            "decisions": [
                "Voice search gets top priority",
                "Assign at least 3 engineers",
            ],
            "action_items": [
                {
                    "task": "Lead voice search module development",
                    "owner": "Amit",
                    "deadline": "not specified",
                }
            ],
            "key_points": [
                "Voice search is top priority for next quarter",
                "Team size minimum three engineers",
            ],
        },
    },
    {
        "title": "Sales Weekly Sync",
        "transcript": "Humne pichle hafte ke leads review kiye. South region mein conversion achha raha but north region weak tha. We decided to run a focused campaign in Delhi NCR. Nisha will share revised sales targets by Monday.",
        "summary": {
            "summary": "Weekly sales sync reviewed regional conversion and planned a Delhi NCR campaign.",
            "decisions": [
                "Run focused campaign in Delhi NCR",
                "Revise sales targets for north region",
            ],
            "action_items": [
                {
                    "task": "Share revised sales targets",
                    "owner": "Nisha",
                    "deadline": "Monday",
                }
            ],
            "key_points": [
                "South region performance strong",
                "North region needs corrective push",
            ],
        },
    },
    {
        "title": "Hiring Planning Meeting",
        "transcript": "Engineering team mein hiring slow chal rahi hai. We need backend and data engineers urgently. Team decided to close two backend roles and one data role this month. Arjun will coordinate with HR and schedule interviews this week.",
        "summary": {
            "summary": "Hiring planning focused on urgent backend and data engineering positions.",
            "decisions": [
                "Close two backend roles this month",
                "Close one data engineer role this month",
            ],
            "action_items": [
                {
                    "task": "Coordinate with HR and schedule interviews",
                    "owner": "Arjun",
                    "deadline": "this week",
                }
            ],
            "key_points": [
                "Hiring pace is currently slow",
                "Backend and data roles are urgent",
            ],
        },
    },
    {
        "title": "Customer Support Review",
        "transcript": "Support tickets ka response time increase ho gaya hai. We agreed to introduce a priority queue for enterprise customers and add one evening shift. Meera will draft the updated support rota by Wednesday.",
        "summary": {
            "summary": "Customer support review identified response delay and approved process changes.",
            "decisions": [
                "Introduce enterprise priority queue",
                "Add one evening support shift",
            ],
            "action_items": [
                {
                    "task": "Draft updated support rota",
                    "owner": "Meera",
                    "deadline": "Wednesday",
                }
            ],
            "key_points": [
                "Ticket response time has increased",
                "Operational changes approved",
            ],
        },
    },
]


def main() -> None:
    for item in fake_meetings:
        meeting_id = save_meeting(
            transcript=item["transcript"],
            summary=item["summary"],
            title=item["title"],
        )
        print(f"Seeded {item['title']} -> {meeting_id}")


if __name__ == "__main__":
    main()
